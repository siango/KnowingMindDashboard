(function(){
  const fmt = n => n.toLocaleString('en-US')
  document.getElementById('now').textContent = new Date().toLocaleString()
  document.getElementById('yr').textContent = new Date().getFullYear()

  // ตัวเลข mock
  const m = { users: 1287, sessions: 4312, conv: 0.076, errors: 12 }
  document.getElementById('m1').textContent = fmt(m.users)
  document.getElementById('m2').textContent = fmt(m.sessions)
  document.getElementById('m3').textContent = (m.conv*100).toFixed(1) + '%'
  document.getElementById('m4').textContent = fmt(m.errors)

  // กราฟ
  const labels = Array.from({length:7}, (_,i)=>['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][ (new Date().getDay()-6+i+7)%7 ])
  const traffic = labels.map(()=> Math.round(800 + Math.random()*800))
  const ctx1 = document.getElementById('chartTraffic')
  new Chart(ctx1,{type:'line',data:{labels,datasets:[{label:'Visits',data:traffic,tension:.35}]},
    options:{responsive:true,plugins:{legend:{display:false}}}})

  const types = ['View','Click','Share','Signup']
  const events = types.map(()=> Math.round(50+Math.random()*300))
  const ctx2 = document.getElementById('chartEvents')
  new Chart(ctx2,{type:'bar',data:{labels:types,datasets:[{label:'Events',data:events}]} ,
    options:{responsive:true,plugins:{legend:{display:false}}}})

  // ตาราง
  const rows = Array.from({length:8},(_,i)=>({
    name:'Item #'+(i+1),
    owner:['team-a','team-b','ops','ml'][i%4],
    status:['OK','WARN','OK','OK','OK','WARN','OK','OK'][i%8],
    updated:new Date(Date.now()-i*3600e3).toLocaleString()
  }))
  const tb = document.querySelector('#tbl tbody')
  rows.forEach(r=>{
    const tr = document.createElement('tr')
    tr.innerHTML = <td></td><td></td><td></td><td></td>
    tb.appendChild(tr)
  })
})();
