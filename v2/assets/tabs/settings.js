import { fetchJSON, getBase } from '../lib/net.js';
export async function render(mount){
  mount.innerHTML=<div class='card' style='padding:20px'>
    <div class='card-title'>Settings</div>
    <label class='muted'>API Base (เช่น https://api.yourdomain.com/kms)</label>
    <input id='api' class='search' style='width:100%' placeholder='https://your.api/kms'/>
    <div style='margin-top:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap'>
      <button id='save' class='tab'>Save</button>
      <button id='testAll' class='tab'>Test All</button>
      <span id='status' class='muted'></span>
    </div>
    <div style='margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap'>
      <div>Overview: <span id='badgeD' class='badge badge-mock'>MOCK</span></div>
      <div>Analytics: <span id='badgeA' class='badge badge-mock'>MOCK</span></div>
      <div>Reports: <span id='badgeR' class='badge badge-mock'>MOCK</span></div>
    </div>
  </div>;
  const key='kms_api_base', input=document.getElementById('api'), status=document.getElementById('status');
  input.value=getBase();
  const setBadge=(id,live)=>{const b=document.getElementById(id); b.textContent=live?'LIVE':'MOCK'; b.className=\adge \\;}
  document.getElementById('save').onclick=()=>{ localStorage.setItem(key,input.value.trim()); status.textContent='Saved'; };
  document.getElementById('testAll').onclick=async()=>{ status.textContent='Testing…'; const base=(input.value||'').replace(/\/+$/,''); if(!base){ status.textContent='Please set base first'; return; }
    const [d,a,r]=await Promise.all([fetchJSON(\\C:\Users\SIANG~1.AUS\AppData\Local\Temp\kms-ghpages-fix-20250912-201826/dashboard\),fetchJSON(\\C:\Users\SIANG~1.AUS\AppData\Local\Temp\kms-ghpages-fix-20250912-201826/analytics/summary\),fetchJSON(\\C:\Users\SIANG~1.AUS\AppData\Local\Temp\kms-ghpages-fix-20250912-201826/reports\)]);
    setBadge('badgeD',!!d.ok); setBadge('badgeA',!!a.ok); setBadge('badgeR',!!r.ok); status.textContent='Done';
  };
}
