const $ = (s,c=document)=>c.querySelector(s);
const tick=()=>{ #now.textContent=new Date().toLocaleString(); #yr.textContent=new Date().getFullYear(); };
const routes={
  overview: () => import('./tabs/overview.js'),
  analytics:() => import('./tabs/analytics.js'),
  reports:  () => import('./tabs/reports.js'),
  settings: () => import('./tabs/settings.js'),
};
async function renderRoute(){
  tick();
  const hash=location.hash.replace(/^#\//,'')||'overview';
  document.querySelectorAll('.tab').forEach(a=>a.classList.toggle('active',a.dataset.tab===hash));
  const mount=#app; mount.innerHTML=<div class="card" style="padding:24px">Loading <b></b>…</div>;
  try{
    if(!routes[hash]) throw new Error('Not Found');
    const mod=await routes[hash](); await mod.render(mount);
  }catch(e){ console.error(e);
    mount.innerHTML=<div class="card" style="padding:24px;color:#fca5a5">Oops: </div>; }
}
addEventListener('hashchange',renderRoute); addEventListener('DOMContentLoaded',renderRoute);
