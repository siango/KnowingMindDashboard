(function(){
  const $=(q,r=document)=>r.querySelector(q); const }=(q,r=document)=>Array.from(r.querySelectorAll(q));
  const VER=window.KM_VER||'20250913-101857'; const content=#content;
  function setActive(h){ }(".nav-link").forEach(a=>a.classList.toggle('active',a.getAttribute('href')===h)); }
  const esc=s=>(''+s).replace(/[&<>\"']/g,m=>({ '&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;', \"'\":'&#39;' }[m]));

  async function fetchWithTimeout(url,ms=8000){
    const c=new AbortController(); const t=setTimeout(()=>c.abort(),ms);
    try{ const r=await fetch(url,{cache:'no-store',signal:c.signal}); clearTimeout(t);
      if(!r.ok) throw new Error(HTTP   for https://siango.github.io/KnowingMindDashboard/v2/#/overview); return await r.text();
    }catch(e){ clearTimeout(t); throw e; }
  }

  async function loadTab(name){
    try{
      setActive('#/'+name);
      content.innerHTML=<div class="loading">Loading …</div>;
      const html = await fetchWithTimeout(	abs/.html?v=20250913-101857, 8000);
      content.innerHTML = html;
      }(".progress[data-value] > span",content).forEach(s=>{ const v=Number(s.parentElement.getAttribute('data-value')||0); requestAnimationFrame(()=>s.style.width=Math.min(100,Math.max(0,v))+'%'); });
    }catch(err){
      content.innerHTML = <div class='card'><h3>Load error</h3><pre>\</pre><p class='muted'>This often happens when an old Service Worker cached the site. Try Ctrl+Shift+R or open in Incognito.</p>
      <p class='muted'>Direct test: <code>tabs/overview.html?v=\20250913-101857</code></p></div>;
      console.error(err);
    }
  }

  function route(){ const name=(location.hash.replace(/^#\\//,'')||'overview'); loadTab(name); }
  window.addEventListener('hashchange',route); document.addEventListener('DOMContentLoaded',route);
})();
