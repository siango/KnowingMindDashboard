import { fetchJSON, getBase } from '../lib/net.js';

export async function render(mount){
  mount.innerHTML=<div class='card' style='padding:20px'>
    <div class='card-title'>Settings</div>
    <label class='muted'>API Base (เช่น https://api.yourdomain.com/kms)</label>
    <input id='api' class='search' style='width:100%' placeholder='https://your.api/kms'/>
    <div style='margin-top:10px'>
      <button id='save' class='tab'>Save</button>
      <button id='test' class='tab'>Test /dashboard</button>
      <span id='status' class='muted'></span>
    </div>
  </div>;
  const key='kms_api_base', input=document.getElementById('api'), status=document.getElementById('status');
  input.value=getBase();
  document.getElementById('save').onclick=()=>{ localStorage.setItem(key,input.value.trim()); status.textContent='Saved'; };
  document.getElementById('test').onclick=async()=>{
    const base=(input.value||'').replace(/\/+$/,''); if(!base){ status.textContent='Please set base first'; return; }
    status.textContent='Testing…'; const {ok,error}=await fetchJSON(\\C:\Users\SIANG~1.AUS\AppData\Local\Temp\kms-ghpages-fix-20250912-201826/dashboard\);
    status.textContent= ok ? 'OK' : ('Fail: '+error);
  };
}
