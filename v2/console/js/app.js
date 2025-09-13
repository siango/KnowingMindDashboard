(function(){
  const K=window.KMS, R=window.KMSRoute, G=window.KMSGAS;

  // helpers: refresh / hard refresh
  const KMSHelpers={
    reload(){ const u=new URL(location.href); u.searchParams.set('v',Date.now().toString()); location.href=u.toString(); },
    async hardReload(){ try{ if('caches' in window){ for(const k of await caches.keys()) await caches.delete(k); } }catch(e){} try{localStorage.clear();sessionStorage.clear();}catch(e){} this.reload(); }
  };
  window.KMSHelpers=KMSHelpers;

  function buildProjectsSubnav(){
    const box=document.getElementById('projects-subnav'); if(!box) return; box.innerHTML='';
    for(const p of R.PROJECTS){
      const a=document.createElement('a'); a.href='#/proj/'+p.id; a.dataset.proj=p.id; a.textContent=p.title;
      a.addEventListener('click',e=>{e.preventDefault(); R.goProj(p.id); render();}); box.appendChild(a);
    }
  }
  async function loadHealth(){
    const box=document.getElementById('health-list'); if(!box) return; box.innerHTML='';
    for(const svc of Object.values(K.SERVICES||{})){
      const li=document.createElement('li'); li.textContent=${svc.name} …; box.appendChild(li);
      try{ const r=await G.ping(svc.url); li.textContent=${svc.name}:  (); li.className=r.ok?'ok':'err'; }
      catch(e){ li.textContent=${svc.name}: ✗ ERR (net); li.className='err'; }
    }
  }
  async function fetchData(kind){
    const mode=(K.DATA_MODE||'auto'); const wantGas=(mode==='gas'||mode==='auto'); const wantApi=(mode==='api'||mode==='auto');
    async function tryGas(){ return await G.gasFetch({action:get_},'GET'); }
    async function tryMock(){ const r=await fetch(./data/mock_.json?ver=); return await r.json(); }
    if(wantGas){ try{ return await tryGas(); }catch(e){} } if(wantApi){ /* reserved */ } return await tryMock();
  }
  function highlight(route){
    document.querySelectorAll('aside a[data-nav]').forEach(a=>a.classList.toggle('active',route.type==='top'&&a.dataset.nav===route.tab));
    document.querySelectorAll('#projects-subnav a[data-proj]').forEach(a=>a.classList.toggle('active',route.type==='project'&&a.dataset.proj===route.id));
  }

  async function render(){
    const route=R.normalize(); highlight(route);
    const main=document.getElementById('main');

    if(route.type==='top'){
      if(route.tab==='overview'){
        const d=await fetchData('overview');
        main.innerHTML=<h2>Overview</h2>
        <div class="grid">
          <div class="card"><h3>Live Services</h3><ul id="health-list"></ul></div>
          <div class="card"><h3>Today</h3><p></p></div>
          <div class="card"><h3>Progress</h3><p></p></div>
        </div>; loadHealth(); return;
      }
      if(route.tab==='analytics'){
        const d=await fetchData('analytics');
        main.innerHTML=<h2>Analytics</h2><div class="card"><pre></pre></div>; return;
      }
      if(route.tab==='projects'){
        main.innerHTML=<h2>Projects</h2><div class="card"><p>เลือกโปรเจกต์ทางซ้าย หรือเปิดตรงด้วย <code>#/proj/&lt;slug&gt;</code></p></div>; return;
      }
      if(route.tab==='commands'){
        main.innerHTML=<h2>Commands (read-only)</h2>
        <div class="card"><ul class="bullets">
          <li>OSP9: Deploy Dashboard (/v2/console)</li>
          <li>OSP9: Pages Cache Bust</li>
          <li>OST9: Sync Chat Snapshot → Data Hub</li>
        </ul></div>; return;
      }
      if(route.tab==='settings'){
        main.innerHTML=<h2>Settings</h2>
        <div class="card">
          <p>VERSION: <code></code></p>
          <button onclick="KMSHelpers.reload()">Reload with Version</button>
          <button onclick="KMSHelpers.hardReload()">Clear Cache & Reload</button>
        </div>; return;
      }
    }

    // Project page
    const p=R.PROJECTS.find(x=>x.id===route.id);
    if(!p){ main.innerHTML=<h2>Not found</h2><div class="card">Unknown project id: <code></code></div>; return; }
    main.innerHTML=<h2>Project · </h2>
    <div class="grid">
      <div class="card"><h3>Status</h3><p>Console v2 stub. เชื่อมฐานข้อมูลกลางเมื่อพร้อม</p></div>
      <div class="card"><h3>Quick Links</h3><ul class="bullets"><li><a href="#/projects">All projects</a></li></ul></div>
    </div>;
  }

  function boot(){ buildProjectsSubnav(); window.addEventListener('hashchange',render); render(); }
  document.addEventListener('DOMContentLoaded',boot);
})();