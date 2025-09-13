/* KM-OV-BOOT (safe) */
(() => {
  const $  = (q,d=document)=>d.querySelector(q);
  const $$ = (q,d=document)=>Array.from(d.querySelectorAll(q));
  const VER = (window.KM_VER||'') + '';
  const DATA_BASE = './data/';
  async function getJSON(n){ const u=DATA_BASE+n+(VER?('?v='+VER):''); const r=await fetch(u,{cache:'no-store'}); if(!r.ok) throw new Error('HTTP '+r.status+' '+u); return r.json(); }
  window.KM_OV = async function(){
    const el = $('#content'); if(!el) return;
    el.innerHTML = '<div class="card glass" style="padding:16px">Loading overview...</div>';
    try{
      const idx = await getJSON('projects_index.json');
      const cards = (idx.items||[]).map(it=>`
        <a class="card glass" href="#/${it.id}" style="padding:14px;display:block;text-decoration:none">
          <h3 style="margin:0 0 6px 0">${it.name}</h3>
          <p class="muted" style="margin:0">ดูรายละเอียดโครงการ</p>
        </a>`).join('');
      el.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px">${cards}</div>`;
    }catch(e){
      el.innerHTML = '<div class="card glass" style="padding:16px"><h3>Overview error</h3><pre>'+String(e.message||e)+'</pre></div>';
    }
  };
  if ((location.hash||'').replace(/^#\/?/,'')==='overview') window.KM_OV();
})();
