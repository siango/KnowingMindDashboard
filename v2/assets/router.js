const $ = (s,c=document)=>c.querySelector(s);
const routes = {
  overview : () => import("./tabs/overview.js"),
  analytics: () => import("./tabs/analytics.js"),
  reports  : () => import("./tabs/reports.js"),
  settings : () => import("./tabs/settings.js")
};
let disposer = null;

function activateTab(hash){
  document.querySelectorAll(".tab").forEach(a=>{
    a.classList.toggle("active", ("#/"+(a.dataset.tab||"")) === hash);
  });
}

async function renderRoute(){
  const hash = location.hash || "#/overview";
  activateTab(hash);
  const mount = $("#app");
  mount.innerHTML = `<div class="card">Loading <b>${hash.replace('#/','')}</b>…</div>`;
  if (typeof disposer === "function"){ try{ disposer(); }catch{} disposer=null; }
  try{
    const key = hash.replace(/^#\//,'') || "overview";
    const mod = await routes[key]();
    const fn = await mod.render(mount);
    if (typeof fn === "function") disposer = fn;
  }catch(e){
    console.error(e);
    mount.innerHTML = `<div class="card red">Error: ${e.message}</div>`;
  }
}
addEventListener("hashchange", renderRoute);
addEventListener("DOMContentLoaded", renderRoute);
