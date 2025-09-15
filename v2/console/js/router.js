(function(){
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.getRegistrations().then(rs => rs.forEach(r=>r.unregister()));
  }
  const mount=()=>document.getElementById('app')||(function(){const d=document.createElement('div');d.id='app';document.body.appendChild(d);return d})();
  const routes={'#/overview':'tabs/overview.html','#/projects':'tabs/projects.html','#/dhamma-tri':'tabs/dhamma-tri.html'};
  const cur = ()=>location.hash||'#/overview';
  async function load(){
    const f = routes[cur()]||routes['#/overview'];
    try{
      const res = await fetch(f+'?v='+Date.now(),{cache:'no-store'});
      mount().innerHTML = await res.text();
    }catch(e){ mount().innerHTML = '<p style="color:#bbb">โหลดไม่สำเร็จ: '+(e&&e.message||e)+'</p>'; }
  }
  addEventListener('hashchange',load); addEventListener('DOMContentLoaded',load);
})();
