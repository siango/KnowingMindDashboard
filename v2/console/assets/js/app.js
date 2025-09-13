(function(){
  const $=(q,r=document)=>r.querySelector(q);
  const }=(q,r=document)=>Array.from(r.querySelectorAll(q));
  const VER = window.KM_VER || '';
  const content = #content;

  function setActive(hash){ }(".nav-link").forEach(a=>a.classList.toggle("active", a.getAttribute("href")===hash)); }
  function esc(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[c])); }

  async function fetchWithTimeout(url, ms=10000){
    const ctrl = new AbortController(); const t = setTimeout(()=>ctrl.abort(), ms);
    try{
      const res = await fetch(url, {cache:"no-store", signal: ctrl.signal});
      clearTimeout(t);
      if(!res.ok) throw new Error(\HTTP \ \ for \https://siango.github.io/KnowingMindDashboard/v2/#/overview\);
      return await res.text();
    }catch(e){ clearTimeout(t); throw e; }
  }

  async function loadTab(name){
    try{
      setActive("#/"+name);
      content.innerHTML = \<div class="loading">Loading \…</div>\;
      const html = await fetchWithTimeout(\	abs/\.html?v=\20250913-062804\, 10000);
      content.innerHTML = html;
      }(".progress[data-value] > span", content).forEach(span=>{
        const v = Number(span.parentElement.getAttribute("data-value")||0);
        requestAnimationFrame(()=>{ span.style.width = Math.min(100,Math.max(0,v)) + "%"; });
      });
    }catch(err){
      content.innerHTML = "<div class='card'><h3>Load error</h3><pre>"+esc(err.message||err)+"</pre><p class='muted'>Hard reload (Ctrl+Shift+R) หรือ Incognito ช่วยเคลียร์ SW/แคช</p></div>";
      console.error(err);
    }
  }

  function route(){ const name = (location.hash.replace(/^#\\//,'') || 'overview'); loadTab(name); }
  window.addEventListener("hashchange", route);
  document.addEventListener("DOMContentLoaded", route);
})();
