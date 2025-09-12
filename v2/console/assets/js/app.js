(function(){
  const $ = (q,root=document)=>root.querySelector(q);
  const v2/console = (q,root=document)=>Array.from(root.querySelectorAll(q));
  const VER = (window.KM_VER||'20250913-063229');
  const content = #content;

  function setActive(hash){ v2/console(".nav-link").forEach(a=>a.classList.toggle("active", a.getAttribute("href")===hash)); }

  async function loadTab(name){
    setActive(#/+name);
    content.innerHTML = <div class="loading">Loading …</div>;
    const url = 	abs/.html?v=20250913-063229;
    const res = await fetch(url, {cache:"no-store"});
    const html = await res.text();
    content.innerHTML = html;
    v2/console(".progress[data-value] > span", content).forEach(span=>{
      const v = Number(span.parentElement.getAttribute("data-value")||0);
      requestAnimationFrame(()=>{ span.style.width = Math.min(100,Math.max(0,v)) + "%"; });
    });
  }

  function route(){
    const name = (location.hash.replace(/^#\\//,'') || 'overview');
    loadTab(name).catch(e=>{ content.innerHTML = \<div class="card"><h3>Not Found</h3><p class="muted">\</p></div>\; });
  }
  window.addEventListener("hashchange", route);
  document.addEventListener("DOMContentLoaded", route);
})();
