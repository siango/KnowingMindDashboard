(function(){
  const mount = () => document.getElementById('app') || (function(){ const d=document.createElement('div'); d.id='app'; document.body.appendChild(d); return d })();
  const routes = {
    '#/overview':'tabs/overview.html',
    '#/projects':'tabs/projects.html',
    '#/dhamma-tri':'tabs/dhamma-tri.html'
  };
  const cur = () => location.hash || '#/overview';
  async function load(){
    const r = routes[cur()] || routes['#/overview'];
    try{
      const res = await fetch(r + '?v=' + Date.now());
      const html = await res.text();
      mount().innerHTML = html;
    }catch(e){
      mount().innerHTML = '<p style="color:#bbb">โหลดไม่สำเร็จ: '+(e&&e.message||e)+'</p>';
    }
  }
  addEventListener('hashchange', load);
  addEventListener('DOMContentLoaded', load);
})();
