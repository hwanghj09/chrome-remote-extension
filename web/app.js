'use strict';
const $ = id => document.getElementById(id);
const FIELDS = {
  get_status: [], list_tabs: [], list_windows: [],
  open_tab: [['url','URL','url','https://example.com/']],
  new_window: [['url','URL','url','https://example.com/']],
  navigate_tab: [['tab_id','탭 ID','number',''],['url','URL','url','https://example.com/']],
  close_tab: [['tab_id','탭 ID','number','']],
  activate_tab: [['tab_id','탭 ID','number','']],
  reload_tab: [['tab_id','탭 ID','number','']],
  duplicate_tab: [['tab_id','탭 ID','number','']],
  pin_tab: [['tab_id','탭 ID','number',''],['pinned','고정 여부','boolean','true']],
  mute_tab: [['tab_id','탭 ID','number',''],['muted','음소거 여부','boolean','true']],
  set_zoom: [['tab_id','탭 ID','number',''],['zoom','배율 (0.25 ~ 5)','decimal','1.25']],
  focus_window: [['window_id','창 ID','number','']],
  close_window: [['window_id','창 ID','number','']],
  notify: [['title','제목','text','안내'],['message','내용','text','테스트 알림입니다.']]
};
let adminKey = '';
let devices = [];
let selected = null;
let polling = false;
let toastTimer = null;
function toast(message) {
  const node = $('toast');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {node.hidden = true;}, 4200);
}
async function api(path, options={}) {
  const res = await fetch(path, {
    ...options,
    cache: 'no-store',
    headers: {...(options.body ? {'Content-Type':'application/json'} : {}), 'X-Admin-Key': adminKey, ...(options.headers || {})}
  });
  let payload;
  try { payload = await res.json(); } catch { payload = {}; }
  if (!res.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `요청 실패 (HTTP ${res.status})`);
  return payload;
}
function showConsole(visible) {
  $('authPanel').hidden = visible;
  $('consolePanel').hidden = !visible;
}
function renderDevices() {
  $('totalDevices').textContent = String(devices.length);
  $('onlineDevices').textContent = String(devices.filter(d=>d.online).length);
  $('selectedDeviceNumber').textContent = selected || '—';
  const root = $('devices'); root.replaceChildren();
  if (!devices.length) { const p=document.createElement('p');p.className='muted';p.textContent='등록된 기기가 없습니다. 등록 코드를 먼저 발급하세요.';root.append(p); }
  for (const d of devices) {
    const button=document.createElement('button');button.type='button';button.className='device'+(d.device_id===selected?' selected':'');
    const left=document.createElement('span');left.className='device-name';
    const number=document.createElement('span');number.className='device-id';number.textContent='#'+d.device_id;
    const title=document.createElement('span');title.className='device-title';title.textContent=d.name;
    left.append(number,title);
    const status=document.createElement('span');status.className='status'+(d.online?' online':'');status.textContent=d.online?'온라인':'오프라인';
    button.append(left,status);
    button.addEventListener('click',()=>{selected=d.device_id;renderDevices();renderSelection();});
    root.append(button);
  }
  renderSelection();
}
function renderSelection() {
  const d=devices.find(x=>x.device_id===selected);
  $('controlSubtitle').textContent=d ? `${d.name} · ${d.online?'접속됨':'오프라인'}` : '왼쪽에서 크롬북을 선택하세요.';
  $('targetBadge').textContent=d?'#'+d.device_id:'미선택';
  const canSend=Boolean(d && d.online);
  $('sendCommand').disabled=!canSend;
  document.querySelectorAll('[data-action]').forEach(b=>{b.disabled=!canSend;});
  $('revokeDevice').disabled=!d;
}
async function refresh() {
  if (!adminKey || polling) return;
  polling=true;
  try {
    devices=await api('/api/admin/devices');
    if(selected && !devices.some(d=>d.device_id===selected)) selected=null;
    renderDevices();
  } catch(e) { toast('기기 목록 갱신 실패: '+e.message); }
  finally { polling=false; }
}
function renderFields() {
  const action=$('action').value;
  const container=$('fields');container.replaceChildren();
  for (const [key,label,type,placeholder] of FIELDS[action]||[]) {
    const wrap=document.createElement('div');wrap.className='stacked';wrap.style.gap='5px';
    const lbl=document.createElement('label');lbl.htmlFor='field-'+key;lbl.textContent=label;
    let control;
    if(type==='boolean') {
      control=document.createElement('select');
      for(const [v,t] of [['true','예 (true)'],['false','아니요 (false)']]) {
        const o=document.createElement('option');o.value=v;o.textContent=t;control.append(o);
      }
    } else {
      control=document.createElement('input');
      control.type=type==='number'||type==='decimal'?'number':type==='url'?'url':'text';
      if(type==='number'){control.min='0';control.step='1';}
      if(type==='decimal'){control.min='.25';control.max='5';control.step='.05';}
      control.placeholder=placeholder;control.value=placeholder&&type!=='url'?placeholder:'';
      control.required=true;
    }
    control.id='field-'+key;control.name=key;wrap.append(lbl,control);container.append(wrap);
  }
}
async function command(action,params) {
  const d=devices.find(x=>x.device_id===selected);
  if(!d||!d.online) { toast('온라인 기기를 먼저 선택하세요.');return; }
  $('resultBox').textContent='명령을 전송하는 중…';
  try {
    const response=await api(`/api/admin/devices/${encodeURIComponent(d.device_id)}/commands`,{
      method:'POST',body:JSON.stringify({action,params})
    });
    $('resultBox').textContent=JSON.stringify(response,null,2)+'\n명령 실행 결과를 기다리는 중…';
    for(let attempt=0;attempt<15;attempt++) {
      await new Promise(resolve=>setTimeout(resolve,800));
      const result=await api(`/api/admin/commands/${encodeURIComponent(response.command_id)}`);
      $('resultBox').textContent=JSON.stringify(result,null,2);
      if(['done','error','delivery_failed'].includes(result.status)) return;
    }
    toast('결과 수신이 지연되고 있습니다. 명령은 전송되었을 수 있습니다.');
  } catch(e) { $('resultBox').textContent='오류: '+e.message;toast(e.message); }
}
$('loginForm').addEventListener('submit',async e=>{
  e.preventDefault(); const key=$('adminKey').value.trim();if(!key)return;
  adminKey=key;
  try {devices=await api('/api/admin/devices');$('adminKey').value='';showConsole(true);renderDevices();}
  catch(e){adminKey='';toast('인증 실패: '+e.message);}
});
$('logout').addEventListener('click',()=>{adminKey='';selected=null;devices=[];showConsole(false);$('resultBox').textContent='명령을 실행하면 여기에 결과가 표시됩니다.';});
$('refreshDevices').addEventListener('click',refresh);
$('action').addEventListener('change',renderFields);
$('commandForm').addEventListener('submit',e=>{
  e.preventDefault();
  const action=$('action').value,params={};
  for(const [key,,type] of FIELDS[action]||[]) {
    const value=$('field-'+key).value;
    if(type==='number')params[key]=Number.parseInt(value,10);
    else if(type==='decimal')params[key]=Number(value);
    else if(type==='boolean')params[key]=value==='true';
    else params[key]=value;
  }
  command(action,params);
});
document.querySelectorAll('[data-action]').forEach(b=>b.addEventListener('click',()=>command(b.dataset.action,{})));
$('enrollForm').addEventListener('submit',async e=>{
  e.preventDefault();try{
    const name=$('enrollName').value.trim();if(!name)throw new Error('기기 이름을 입력하세요.');
    const data=await api('/api/admin/enrollments',{method:'POST',body:JSON.stringify({name})});
    $('enrollmentCode').textContent=data.pairing_code;$('enrollmentOutput').hidden=false;
    toast('등록 코드를 발급했습니다. 승인된 기기에서 입력하세요.');
  }catch(error){toast(error.message);}
});
$('copyCode').addEventListener('click',async()=>{
  try {await navigator.clipboard.writeText($('enrollmentCode').textContent);toast('코드를 복사했습니다.');}
  catch {toast('복사할 수 없습니다. 직접 선택해 복사하세요.');}
});
$('revokeDevice').addEventListener('click',async()=>{
  const d=devices.find(x=>x.device_id===selected);if(!d)return;
  if(!confirm(`기기 #${d.device_id} (${d.name})의 등록을 폐기할까요? 재등록하려면 새 코드가 필요합니다.`))return;
  try {await api(`/api/admin/devices/${encodeURIComponent(d.device_id)}`,{method:'DELETE'});selected=null;await refresh();toast('기기 등록이 폐기되었습니다.');}
  catch(e){toast(e.message);}
});
$('clearResult').addEventListener('click',()=>{$('resultBox').textContent='명령을 실행하면 여기에 결과가 표시됩니다.';});
renderFields();renderSelection();
setInterval(()=>{if(adminKey)refresh();},5000);
