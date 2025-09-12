export async function render(mount){
  mount.innerHTML=
  <section class='grid stats'>
    <div class='card stat'><div class='label'>Active Users</div><div id='m1' class='value'>—</div></div>
    <div class='card stat'><div class='label'>Sessions</div><div id='m2' class='value'>—</div></div>
    <div class='card stat'><div class='label'>Conversion</div><div id='m3' class='value'>—</div></div>
    <div class='card stat'><div class='label'>Errors</div><div id='m4' class='value'>—</div></div>
  </section>
  <section class='grid'>
    <div class='card'><div class='card-title'>Traffic (7d)</div><canvas id='chartTraffic'></canvas></div>
    <div class='card'><div class='card-title'>Events by Type</div><canvas id='chartEvents'></canvas></div>
  </section>
  <section class='card'>
    <div class='card-title'>Recent Items</div>
    <table class='table' id='tbl'><thead><tr><th>Name</th><th>Owner</th><th>Status</th><th>Updated</th></tr></thead><tbody></tbody></table>
  </section>;
  const API_URL = localStorage.getItem('kms_api_endpoint') || '';
  const mock = () => ({activeUsers:1287,sessions:4312,conversion:7.6,errors:12,
    traffic:Array.from({length:7},()=>Math.round(800+Math.random()*800)),
    eventsByType:{View:420,Click:280,Share:90,Signup:44},
    recent:Array.from({length:8},(_,i)=>({name:'Item #'+(i+1),owner:['team-a','ops','ml','web'][i%4],status:['OK','WARN'][i%2],updated:new Date(Date.now()-i*3600e3).toISOString()}))});
  let d; try{
    if(API_URL){ const r=await fetch(API_URL,{headers:{Accept:'application/json'}}); if(!r.ok) throw new Error('HTTP '+r.status); d=await r.json(); }
    else d=mock();
  }catch{ d=mock(); }
  const fmt=n=>typeof n==='number'?n.toLocaleString('en-US'):n;
  m1.textContent=fmt(d.activeUsers); m2.textContent=fmt(d.sessions);
  m3.textContent=(Number(d.conversion)||0).toFixed(1)+'%'; m4.textContent=fmt(d.errors);
  new Chart(chartTraffic,{type:'line',data:{labels:['Sun','Mon','Tue','Wed','Thu','Fri','Sat'],datasets:[{label:'Visits',data:d.traffic||[],tension:.35}]},options:{responsive:true,plugins:{legend:{display:false}}}});
  const ev=Object.entries(d.eventsByType||{}); new Chart(chartEvents,{type:'bar',data:{labels:ev.map(x=>x[0]),datasets:[{label:'Events',data:ev.map(x=>x[1])}]},options:{responsive:true,plugins:{legend:{display:false}}}});
  const tb=document.querySelector('#tbl tbody'); tb.innerHTML='';
  (d.recent||[]).forEach(r=>{const tr=document.createElement('tr'); tr.innerHTML=<td></td><td></td><td></td><td></td>; tb.appendChild(tr);});
}
