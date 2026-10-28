// Fixed command allowlist. No cookies/history, arbitrary script, or silent page capture.
let socket = null;
let heart = null;
let retry = null;
let ready = false;

async function badge(label, color) {
  await chrome.action.setBadgeText({ text: label });
  await chrome.action.setBadgeBackgroundColor({ color });
}

async function connect() {
  const cfg = await chrome.storage.local.get(['serverOrigin', 'deviceId', 'deviceToken', 'enabled']);
  if (!cfg.enabled || !cfg.deviceId || !cfg.deviceToken || !cfg.serverOrigin) {
    await badge('OFF', '#64748b');
    return;
  }
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;

  const server = new URL(cfg.serverOrigin);
  if (!['http:', 'https:'].includes(server.protocol)) return;
  if (server.protocol === 'http:' && !['localhost', '127.0.0.1'].includes(server.hostname)) return;
  server.protocol = server.protocol === 'https:' ? 'wss:' : 'ws:';
  server.pathname = '/ws/device';
  server.search = '';
  ready = false;
  const ws = new WebSocket(server.toString());
  socket = ws;

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: 'auth', device_id: cfg.deviceId, device_token: cfg.deviceToken }));
    clearInterval(heart);
    heart = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
    }, 20000);
  };

  ws.onmessage = async event => {
    try {
      const message = JSON.parse(event.data);
      if (message.type === 'ready') {
        ready = true;
        await badge('ON', '#0d9488');
        return;
      }
      if (!ready || message.type !== 'command' || typeof message.command_id !== 'string') return;
      // A user can pause the extension between receiving and executing a command.
      const latest = await chrome.storage.local.get('enabled');
      if (!latest.enabled) return;
      let response;
      try {
        response = { ok: true, data: await execute(message.action, message.params || {}) };
      } catch (error) {
        response = { ok: false, data: {}, error: String(error.message || error) };
      }
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'result', command_id: message.command_id, ...response }));
      }
    } catch (error) {
      console.warn('Invalid server message', error);
    }
  };

  ws.onclose = async event => {
    clearInterval(heart);
    ready = false;
    if (socket !== ws) return;
    socket = null;
    if (event.code === 1008) {
      await badge('ERR', '#b91c1c');
      return; // Revoked/invalid credentials: do not retry until user re-pairs.
    }
    await badge('OFF', '#64748b');
    clearTimeout(retry);
    retry = setTimeout(connect, 5000);
  };
  ws.onerror = () => ws.close();
}

function safeTab(tab) {
  // Do not upload browsing URLs or page titles.
  return { id: tab.id, window_id: tab.windowId, index: tab.index,
    active: tab.active, pinned: tab.pinned, muted: tab.mutedInfo?.muted || false };
}

function safeWindow(win) {
  return { id: win.id, focused: win.focused, state: win.state, type: win.type };
}

function validateURL(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error('Only normal HTTP/HTTPS URLs are allowed');
  }
  return url.toString();
}

async function execute(action, p) {
  switch (action) {
    case 'get_status': {
      const [tabs, windows, platform] = await Promise.all([
        chrome.tabs.query({}), chrome.windows.getAll(), chrome.runtime.getPlatformInfo()
      ]);
      return { tab_count: tabs.length, window_count: windows.length, os: platform.os };
    }
    case 'list_tabs': return { tabs: (await chrome.tabs.query({})).map(safeTab) };
    case 'open_tab': return { tab: safeTab(await chrome.tabs.create({ url: validateURL(p.url) })) };
    case 'close_tab': await chrome.tabs.remove(p.tab_id); return { closed: true };
    case 'activate_tab': return { tab: safeTab(await chrome.tabs.update(p.tab_id, { active: true })) };
    case 'reload_tab': await chrome.tabs.reload(p.tab_id); return { reloaded: true };
    case 'duplicate_tab': return { tab: safeTab(await chrome.tabs.duplicate(p.tab_id)) };
    case 'navigate_tab': return { tab: safeTab(await chrome.tabs.update(p.tab_id, { url: validateURL(p.url) })) };
    case 'pin_tab': return { tab: safeTab(await chrome.tabs.update(p.tab_id, { pinned: p.pinned === true })) };
    case 'mute_tab': return { tab: safeTab(await chrome.tabs.update(p.tab_id, { muted: p.muted === true })) };
    case 'set_zoom': await chrome.tabs.setZoom(p.tab_id, p.zoom); return { zoom: p.zoom };
    case 'list_windows': return { windows: (await chrome.windows.getAll()).map(safeWindow) };
    case 'new_window': return { window: safeWindow(await chrome.windows.create({ url: validateURL(p.url) })) };
    case 'focus_window': return { window: safeWindow(await chrome.windows.update(p.window_id, { focused: true })) };
    case 'close_window': await chrome.windows.remove(p.window_id); return { closed: true };
    case 'notify':
      await chrome.notifications.create({
        type: 'basic', title: p.title, message: p.message,
        iconUrl: chrome.runtime.getURL('icons/icon.png')
      });
      return { notified: true };
    default: throw new Error('Unsupported command');
  }
}

async function disconnect() {
  clearTimeout(retry);
  clearInterval(heart);
  if (socket) socket.close();
  socket = null;
  ready = false;
  await badge('OFF', '#64748b');
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (['enabled', 'serverOrigin', 'deviceId', 'deviceToken'].some(key => key in changes)) {
    disconnect().then(connect);
  }
});
chrome.alarms.create('reconnect', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'reconnect') connect(); });
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
