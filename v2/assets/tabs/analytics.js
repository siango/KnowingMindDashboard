export async function render(mount){
  mount.innerHTML=<div class='card' style='padding:20px'>
    <div class='card-title'>Analytics</div>
    <p class='muted'>แก้ไฟล์ <code>assets/tabs/analytics.js</code> ได้อิสระ โดยไม่กระทบแท็บอื่น</p>
    <canvas id='chartA'></canvas></div>;
  const data=Array.from({length:10},()=>Math.round(50+Math.random()*150));
  new Chart(chartA,{type:'line',data:{labels:data.map((_,i)=>i+1),datasets:[{label:'Metric A',data,tension:.3}]},
    options:{responsive:true,plugins:{legend:{display:false}}}});
}
