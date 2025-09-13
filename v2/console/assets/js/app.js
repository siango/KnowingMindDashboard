(function(){
  const $ = (q,root=document)=>root.querySelector(q);
  const } = (q,root=document)=>Array.from(root.querySelectorAll(q));
  const VER = (window.KM_VER||'20250913-075852');
  const content = #content;

  function setActive(hash){ }(".nav-link").forEach(a=>a.classList.toggle("active", a.getAttribute("href")===hash)); }
  const esc = s => (''+s).replace(/[&<>"']/g,m=>({ '&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;', \"'\":'&#39;' }[m]));

  async function loadTab(name){
    try{
      setActive(#/+name);
      content.innerHTML = <div class="loading">Loading …</div>;
      const url = 	abs/.html?v=20250913-075852;
      const res = await fetch(url, {cache:'no-store'});
      if(!res.ok){
        throw new Error(HTTP   for https://siango.github.io/KnowingMindDashboard/v2/#/overview);
      }
      const html = await res.text();
      content.innerHTML = html;
      }(".progress[data-value] > span", content).forEach(span=>{
        const v = Number(span.parentElement.getAttribute('data-value')||0);
        requestAnimationFrame(()=>{ span.style.width = Math.min(100,Math.max(0,v)) + '%'; });
      });
    }catch(err){
      content.innerHTML = <div class='card'><h3>Load error</h3><pre>\</pre><p class='muted'>Try hard reload (Ctrl+Shift+R) or open in Incognito.</p></div>;
    }
  }

  function route(){
    const name = (location.hash.replace(/^#\\//,'') || 'overview');
    loadTab(name);
  }
  window.addEventListener('hashchange', route);
  document.addEventListener('DOMContentLoaded', route);
})();
