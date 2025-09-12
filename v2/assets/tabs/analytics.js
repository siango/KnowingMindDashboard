import { fetchJSON, getBase } from '../lib/net.js';
import { LivePoll } from '../lib/live.js';
export async function render(mount){
  mount.innerHTML=<div class='card' style='padding:20px'><div class='header'><div class='card-title'>Analytics (24h)</div><span id='badgeA' class='badge badge-mock'>MOCK</span></div><canvas id='chartA'></canvas></div>;
  const chart=new Chart(document.getElementById('chartA'),{type:'line',data:{labels:[],datasets:[{label:'Events',data:[],tension:.3}]},options:{responsive:true,plugins:{legend:{display:false}}}});
  const mock=()=>({labels:Array.from({length:24},(_,i)=>\\:00\),series:Array.from({length:24},()=>Math.round(50+Math.random()*150))});
  const setBadge=live=>{const b=document.getElementById('badgeA'); b.textContent=live?'LIVE':'MOCK'; b.className=\adge \\;}
  const updateOnce=async()=>{ const base=getBase(); const url=base?\C:\Users\SIANG~1.AUS\AppData\Local\Temp\kms-ghpages-fix-20250912-201826/analytics/summary:''; let d,live=false;
    if(url){ const {ok,data}=await fetchJSON(url); d= ok?(live=true,data):mock(); } else d=mock();
    chart.data.labels=d.labels; chart.data.datasets[0].data=d.series; chart.update(); setBadge(live);
  };
  const poll=new LivePoll(updateOnce,45000); poll.start(); return ()=>poll.stop();
}
