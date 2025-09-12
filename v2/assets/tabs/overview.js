import { fetchJSON, getBase, fmt } from '../lib/net.js';
import { LivePoll } from '../lib/live.js';

export async function render(mount){
  mount.innerHTML=
  <section class='grid stats'>
    <div class='card stat'><div class='label'>Active Users</div><div id='m1' class='value liveable mock'>—</div></div>
    <div class='card stat'><div class='label'>Sessions</div><div id='m2' class='value liveable mock'>—</div></div>
    <div class='card stat'><div class='label'>Conversion</div><div id='m3' class='value liveable mock'>—</div></div>
    <div class='card stat'><div class='label'>Errors</div><div id='m4' class='value liveable mock'>—</div></div>
  </section>
  <section class='grid'>
    <div class='card'><div class='header'><div class='card-title'>Traffic (7d)</div><span id='badgeTraffic' class='badge badge-mock'>MOCK</span></div><canvas id='chartTraffic'></canvas></div>
    <div class='card'><div class='header'><div class='card-title'>Events by Type</div><span id='badgeEvents' class='badge badge-mock'>MOCK</span></div><canvas id='chartEvents'></canvas></div>
  </section>
  <section class='card'>
    <div class='header'><div class='card-title'>Recent Items</div><span id='badgeRecent' class='badge badge-mock'>MOCK</span></div>
    <table class='table' id='tbl'><thead><tr><th>Name</th><th>Owner</th><th>Status</th><th>Updated</th></tr></thead><tbody></tbody></table>
  </section>;

  const trafficChart=new Chart(document.getElementById('chartTraffic'),{type:'line',data:{labels:['Sun','Mon','Tue','Wed','Thu','Fri','Sat'],datasets:[{label:'Visits',data:[],tension:.35}]},options:{responsive:true,plugins:{legend:{display:false}}}});
  const eventsChart=new Chart(document.getElementById('chartEvents'),{type:'bar',data:{labels:[],datasets:[{label:'Events',data:[]}]},options:{responsive:true,plugins:{legend:{display:false}}}});

  const setLive=(live,els,badge)=>{ (els||[]).forEach(e=>{e.classList.toggle('mock',!live);e.classList.toggle('live',!!live)}); if(badge){const b=document.getElementById(badge); if(b){b.textContent=live?'LIVE':'MOCK'; b.className=\adge \\;}} };

  const mock=()=>({activeUsers:1287,sessions:4312,conversion:7.6,errors:12,
    traffic:Array.from({length:7},()=>Math.round(800+Math.random()*800)),
    eventsByType:{View:420,Click:280,Share:90,Signup:44},
    recent:Array.from({length:8},(_,i)=>({name:\Item #\\,owner:['team-a','ops','ml','web'][i%4],status:['OK','WARN'][i%2],updated:new Date(Date.now()-i*3600e3).toISOString()}))});

  const updateOnce=async()=>{
    const base=getBase(); const url=base?${base}/dashboard:''; let data,live=false;
    if(url){ const {ok,data:res}=await fetchJSON(url); if(ok){data=res; live=true}else{data=mock()} } else { data=mock() }
    m1.textContent=fmt(data.activeUsers); m2.textContent=fmt(data.sessions); m3.textContent=(Number(data.conversion)||0).toFixed(1)+'%'; m4.textContent=fmt(data.errors);
    setLive(live,[m1,m2,m3,m4],null);
    trafficChart.data.datasets[0].data=data.traffic||[]; trafficChart.update(); setLive(live,[], 'badgeTraffic');
    const names=Object.keys(data.eventsByType||{}), vals=names.map(k=>data.eventsByType[k]);
    eventsChart.data.labels=names; eventsChart.data.datasets[0].data=vals; eventsChart.update(); setLive(live,[], 'badgeEvents');
    const tb=document.querySelector('#tbl tbody'); tb.innerHTML=''; (data.recent||[]).forEach(r=>{const tr=document.createElement('tr'); tr.innerHTML=\<td>\</td><td>\</td><td>\</td><td>\</td>\; tb.appendChild(tr);}); setLive(live,[], 'badgeRecent');
  };
  const poll=new LivePoll(updateOnce,30000); poll.start(); return ()=>poll.stop();
}
