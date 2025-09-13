/* KM-OV-BOOT */
(()=> {
  const $ = (q,d=document)=>d.querySelector(q);
  const VER = (window.KM_VER||"");
  const DATA_BASE = "./data/";
  async function j(path){
    const r = await fetch(DATA_BASE+path+(VER?("?v="+VER):""),{cache:"no-store"});
    if(!r.ok) throw new Error("HTTP "+r.status+" "+path);
    return r.json();
  }
  async function boot(){
    const host = $("#content") || $("main") || document.body;
    host.innerHTML = '<div class="loading">Loading…</div>';
    try{
      const idx = await j("projects_index.json");
      const items = (idx && idx.items) || [];
      const cards = items.map(it => (
        `<a href="#/${it.id}" class="project-card glass">
           <div class="title">${it.name}</div>
           <div class="muted">${it.id}</div>
         </a>`
      )).join("");
      host.innerHTML = `<section class="overview">
        <h2>Overview</h2>
        <div class="grid">${cards}</div>
      </section>`;
    }catch(e){
      host.innerHTML = `<div class="card"><h3>Overview error</h3><pre>${String(e.message||e)}</pre></div>`;
      console.error(e);
    }
  }
  document.addEventListener("DOMContentLoaded", boot, {once:true});
})();
