const $ = id => document.getElementById(id);
const say = message => { $('message').textContent = message; };

function normalizeOrigin(text) {
  const url = new URL(text.trim());
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' &&
    ['localhost', '127.0.0.1'].includes(url.hostname))) {
    throw new Error('외부 서버는 HTTPS만 사용할 수 있습니다.');
  }
  return url.origin;
}

async function render() {
  const cfg = await chrome.storage.local.get(['deviceId', 'enabled', 'serverOrigin']);
  $('pairSection').hidden = Boolean(cfg.deviceId);
  $('pairedSection').hidden = !cfg.deviceId;
  if (cfg.serverOrigin) $('server').value = cfg.serverOrigin;
  if (cfg.deviceId) {
    $('deviceId').textContent = cfg.deviceId;
    $('toggle').textContent = cfg.enabled ? '원격 제어 일시 정지' : '원격 제어 다시 켜기';
    say(cfg.enabled ? '원격 제어 허용 중 (아이콘 ON 상태를 확인하세요)' : '원격 제어 일시 정지됨');
  }
}

$('pair').addEventListener('click', async () => {
  try {
    const origin = normalizeOrigin($('server').value);
    const url = new URL(origin);
    const pattern = `${url.protocol}//${url.hostname}/*`;
    // Requested only after clicking the explicit opt-in button.
    const permission = await chrome.permissions.request({ origins: [pattern] });
    if (!permission) throw new Error('서버 접근 권한이 허용되지 않았습니다.');

    const response = await fetch(`${origin}/api/pair`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: $('code').value.trim(), device_name: $('name').value.trim() })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || '등록에 실패했습니다.');
    await chrome.storage.local.set({
      serverOrigin: origin, deviceId: result.device_id,
      deviceToken: result.device_token, enabled: true
    });
    await render();
  } catch (error) { say(error.message); }
});

$('toggle').addEventListener('click', async () => {
  const { enabled } = await chrome.storage.local.get('enabled');
  await chrome.storage.local.set({ enabled: !enabled });
  await render();
});

$('forget').addEventListener('click', async () => {
  await chrome.storage.local.remove(['deviceId', 'deviceToken', 'enabled', 'serverOrigin']);
  await render();
  say('이 기기의 로컬 인증을 삭제했습니다. 서버에서는 관리자 명령으로 기기를 폐기해 주세요.');
});

render();
