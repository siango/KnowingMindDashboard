import { fetchJSON, getBase } from '../lib/net.js';
import { LivePoll } from '../lib/live.js';
export async function render(mount){
  mount.innerHTML=<div class='card' style='padding:20px'><div class='header'><div class='card-title'>Reports</div><span id='badgeR' class='badge badge-mock'>MOCK</span></div>
  <table class='table'><thead><tr><th>Title</th><th>Type</th><th>Updated</th></tr></thead><tbody id='list'></tbody></table></div>;
  const mock=()=>([{title:'Weekly Summary',type:'pdf',updated:new Date().toISOString(),url:'./reports/weekly-sample.pdf'},{title:'OKR Q4 Plan',type:'pdf',updated:new Date(Date.now()-86400000).toISOString(),url:'./reports/okr-q4.pdf'}]);
  const setBadge=live=>{const b=document.getElementById('badgeR'); b.textContent=live?'LIVE':'MOCK'; b.className=\adge \\;}
  const updateOnce=async()=>{ const base=getBase(); const url=base?\C:\Users\SIANG~1.AUS\AppData\Local\Temp\kms-ghpages-fix-20250912-201826/reports:''; let items,live=false;
    if(url){ const {ok,data}=await fetchJSON(url); items= ok?(live=true,data):mock(); } else items=mock();
    const tb=document.getElementById('list'); tb.innerHTML=''; items.forEach(x=>{ const tr=document.createElement('tr'); tr.innerHTML=\<td><a href="\" target="_blank">\</a></td><td>\</td><td>\</td>\; tb.appendChild(tr); }); setBadge(live);
  };
  const poll=new LivePoll(updateOnce,60000); poll.start(); return ()=>poll.stop();
}
