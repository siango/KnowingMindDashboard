async function load(){
  const res = await fetch('data/heartbeats.json?ts='+Date.now());
  const j = await res.json().catch(()=>({}));
  const rows = Object.values(j||{}).sort((a,b)=> (b.ts||'').localeCompare(a.ts||''));
  const tb = document.querySelector('#tbl tbody');
  tb.innerHTML = '';
  rows.forEach(r=>{
    const tr=document.createElement('tr');
    tr.className = (r.status==='ready'?'ok':(r.status==='paused'?'':'err'));
    tr.innerHTML = \
      <td>\</td>
      <td>\</td>
      <td>\</td>
      <td>\</td>
      <td><code>\</code></td>
      <td>\</td>\;
    tb.appendChild(tr);
  });
  document.getElementById('updated').textContent = new Date().toLocaleString();
}
document.getElementById('refreshBtn').onclick=load;
const auto=document.getElementById('auto');
auto.onchange=()=>{ if(auto.checked){load(); window._t=setInterval(load,10000)} else { clearInterval(window._t)}};
load();
