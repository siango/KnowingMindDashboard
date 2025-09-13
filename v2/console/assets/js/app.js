(function(){
  const $=(q,r=document)=>r.querySelector(q);
  const $$=(q,r=document)=>Array.from(r.querySelectorAll(q));
  const VER = window.KM_VER || "x";
  const content = document.querySelector("#content");

  function setActive(hash){ document.querySelectorAll(".nav-link").forEach(a=>a.classList.toggle("active", a.getAttribute("href")===hash)); }
  function esc(s){ return String(s).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[c])); }

  async function fetchWithTimeout(url, ms=10000){
    const ctrl = new AbortController();
    const t = setTimeout(()=>ctrl.abort(), ms);
    try{
      const res = await fetch(url, {cache:"no-store", signal: ctrl.signal});
      clearTimeout(t);
      if(!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText} for ${url}`);
      return await res.text();
    }catch(e){ clearTimeout(t); throw e; }
  }

  async function loadTab(name){
    try{
      setActive("#/"+name);
      content.innerHTML = `<div class="loading">Loading ${name}…</div>`;
      const html = await fetchWithTimeout(`tabs/${name}.html?v=${VER}`, 10000);
      content.innerHTML = html;
      document.querySelectorAll(".progress[data-value] > span").forEach(span=>{
        const v = Number(span.parentElement.getAttribute("data-value")||0);
        requestAnimationFrame(()=>{ span.style.width = Math.min(100,Math.max(0,v)) + "%"; });
      });
    }catch(err){
      content.innerHTML = `<div class='card'><h3>Load error</h3><pre>${esc(err.message||err)}</pre><p class='muted'>Hard reload (Ctrl+Shift+R) or Incognito.</p></div>`;
      console.error(err);
    }
  }

  function route(){ const name = (location.hash.replace(/^#\/+/,'') || 'overview'); loadTab(name); }
  window.addEventListener('hashchange', route); window.KM_route = route;
  document.addEventListener('DOMContentLoaded', route);
  window.KM_BOOTED = true;
})();
/* KM_CLOCK_TICK */
(function(){
  function pad(n){ return (n<10?'0':'')+n; }
  function fmtDate(d){
    try{ return d.toLocaleDateString('th-TH',{weekday:'short',day:'2-digit',month:'short',year:'numeric'}); }
    catch(e){ return d.toDateString(); }
  }
  function tick(){
    var el=document.getElementById('km-clock'); if(!el) return;
    var t=el.querySelector('.time'), dt=el.querySelector('.date'), now=new Date();
    try{ t.textContent=now.toLocaleTimeString('th-TH',{hour12:false}); }
    catch(e){ t.textContent=pad(now.getHours())+':'+pad(now.getMinutes())+':'+pad(now.getSeconds()); }
    dt.textContent=fmtDate(now);
  }
  document.addEventListener('DOMContentLoaded',function(){ tick(); setInterval(tick,1000); });
})();
;/* KM-AUTO:APP-EXT */
(async function(){
  const $  = (q,d=document)=>d.querySelector(q);
  const $$ = (q,d=document)=>Array.from(d.querySelectorAll(q));
  const VER = (window.KM_VER||'') + '';
  const DATA_BASE = './data/';

  async function getJSON(f){ const u = DATA_BASE + f + (VER?('?v='+VER):''); const r = await fetch(u,{cache:'no-store'}); if(!r.ok) throw new Error('HTTP '+r.status+' '+u); return await r.json(); }

  async function loadProject(id){
    const content = $('#content'); content.innerHTML = '<div class="loading">Loading…</div>';
    try{
      const p = await getJSON(id+'.json');
      const badge = (s)=> s==='green' ? 'live' : (s==='yellow'?'mock':'stale');
      const pct   = (s)=> s==='green'?88:(s==='yellow'?55:25);
      content.innerHTML = `
        <div class="card glass" style="padding:1rem 1.25rem">
          <h2 style="margin:.2rem 0 1rem 0">${p.name} <span class="badge ${badge(p.status)}">${p.status}</span></h2>
          <div style="margin:.5rem 0 1rem 0" class="muted">${p.latest}</div>
          <div class="progress" data-value="${pct(p.status)}"><span></span></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px">
            <div class="card glass" style="padding:12px"><h3>Architecture</h3><p class="muted">${p.architecture}</p></div>
            <div class="card glass" style="padding:12px"><h3>Risks</h3><p class="muted">${p.risks||'-'}</p></div>
          </div>
          <div class="card glass" style="padding:12px;margin-top:16px">
            <h3>Next steps</h3>
            <ul>
              <li><b>48ชม.</b> ${p.next_steps.h48||'-'}</li>
              <li><b>7วัน</b> ${p.next_steps.d7||'-'}</li>
              <li><b>30วัน</b> ${p.next_steps.d30||'-'}</li>
            </ul>
            <p class="muted">updated at ${p.updated_at||''}</p>
          </div>
        </div>`;
      requestAnimationFrame(()=>{$('.progress>span',content).style.width = Math.max(0,Math.min(100, Number($('.progress',content).dataset.value||0)))+'%';});
      $$('.nav-link').forEach(a=>a.classList.toggle('active', a.getAttribute('href')==='#/'+id));
    }catch(e){
      content.innerHTML = '<div class="card"><h3>Load error</h3><pre>'+String(e.message||e)+'</pre></div>';
      console.error(e);
    }
  }

  try{
    const holder = document.querySelector('.sidebar nav'); if(holder){
      const resp = await fetch('./_auto_tabs_block.html'+(VER?('?v='+VER):''),{cache:'no-store'});
      if(resp.ok){ holder.innerHTML = await resp.text(); }
    }
  }catch(_){}

  function route(){ const id = (location.hash.replace(/^#\\/?/,'') || 'kma'); loadProject(id); }
  window.addEventListener('hashchange', route); window.KM_route = route;
  document.addEventListener('DOMContentLoaded', route);
}());

;/* KM-AUTO:I18N */
(()=>{ 
  const SUP = ['en','th','zh']; const KEY='km_lang'; const DEF='en';
  let BUSY=false, cache={};
  const VER=(window.KM_VER||'')+'';   // cache-bust
  const lang = ()=> localStorage.getItem(KEY) || DEF;

  async function loadLang(l){
    if (cache[l]) return cache[l];
    const ctl = new AbortController(); const t = setTimeout(()=>ctl.abort(), 6000);
    try{
      const r = await fetch(`./assets/i18n/${l}.json${VER?`?v=${VER}`:''}`, {cache:'no-store', signal:ctl.signal});
      if(!r.ok) throw new Error('HTTP '+r.status);
      cache[l] = await r.json(); return cache[l];
    }catch(e){
      console.warn('i18n load failed for', l, e);
      if(l!=='en') return loadLang('en');  // fallback ปลอดภัย
      return (cache.en ||= {});
    }finally{ clearTimeout(t); }
  }
  function t(k){ const d = cache[lang()]||{}; return (d[k]??k); }

  async function setLang(l){
    if(BUSY) return; BUSY=true;
    if(!SUP.includes(l)) l=DEF;
    localStorage.setItem(KEY,l);
    document.documentElement.setAttribute('lang', l);
    await loadLang(l);
    // re-render หน้าเดิมแบบไม่แตะ hash (กันลูป)
    if (window.KM_route) window.KM_route();
    BUSY=false;
  }

  // wire ปุ่มธงที่มี data-lang
  document.addEventListener('click', (e)=>{
    const btn = e.target.closest('[data-lang]');
    if(!btn) return;
    e.preventDefault();
    setLang(btn.getAttribute('data-lang'));
  });

  // boot ครั้งแรก
  loadLang(lang()).then(()=>window.dispatchEvent(new Event('km:lang-ready')));
  // เผยให้ส่วนอื่นเรียกได้
  window.KM_i18n = { t, setLang, lang, loadLang };
})();
