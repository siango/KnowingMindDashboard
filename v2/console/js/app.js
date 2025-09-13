(function(){
  const K = window.KMS, R = window.KMSRoute, G = window.KMSGAS;

  async function loadHealth(){
    const box = document.getElementById('health-list');
    box.innerHTML = '';
    const items = Object.values(K.SERVICES||{});
    for(const svc of items){
      const li = document.createElement('li'); li.textContent = ${svc.name} …;
      box.appendChild(li);
      try{
        const r = await G.ping(svc.url);
        li.textContent = ${svc.name}:  ();
        li.className = r.ok? 'ok':'err';
      }catch(e){ li.textContent = ${svc.name}: ✗ ERR (net); li.className='err'; }
    }
  }

  async function fetchData(kind){
    const mode = (K.DATA_MODE||'auto');
    const wantGas = (mode==='gas'||mode==='auto');
    const wantApi = (mode==='api'||mode==='auto');

    async function tryGas(){ return await G.gasFetch({ action:get_ }, 'GET'); }
    async function tryMock(){ const r = await fetch(./data/mock_.json?ver=); return await r.json(); }

    if(wantGas){ try{ return await tryGas(); }catch(e){ /* fallthrough */ } }
    if(wantApi){ /* reserve for future central DB API */ }
    return await tryMock();
  }

  function driveSidebar(){
    document.querySelectorAll('[data-nav]')
      .forEach(a=>a.addEventListener('click', e=>{ e.preventDefault(); R.go(a.dataset.nav); render(); }));
  }

  async function render(){
    const tab = R.get();
    document.querySelectorAll('aside a').forEach(a=>{
      a.classList.toggle('active', a.dataset.nav===tab);
    });

    const main = document.getElementById('main');
    if(tab==='overview'){
      const data = await fetchData('overview');
      main.innerHTML = 
        <h2>Overview</h2>
        <div class="grid">
          <div class="card"><h3>Live Services</h3><ul id="health-list"></ul></div>
          <div class="card"><h3>Today</h3><p></p></div>
          <div class="card"><h3>Progress</h3><p></p></div>
        </div>;
      loadHealth();
    }
    else if(tab==='analytics'){
      const data = await fetchData('analytics');
      main.innerHTML = 
        <h2>Analytics</h2>
        <div class="card"><pre></pre></div>;
    }
    else if(tab==='projects'){
      main.innerHTML = 
        <h2>Projects</h2>
        <ul class="bullets">
          <li>KMA — KnowingMindApp</li>
          <li>ArunRoo — Daily Quotes & Readcast</li>
          <li>SatiShift — Temple duty scheduling</li>
          <li>Beyond the Rich — Sports & finance signals</li>
        </ul>;
    }
    else if(tab==='commands'){
      main.innerHTML = 
        <h2>Commands (read‑only list)</h2>
        <div class="card"><ul class="bullets">
          <li>OSP9: Deploy Dashboard (/v2/console)</li>
          <li>OSP9: Pages Cache Bust</li>
          <li>OST9: Sync Chat Snapshot → Data Hub</li>
        </ul></div>;
    }
    else if(tab==='settings'){
      main.innerHTML = 
        <h2>Settings</h2>
        <div class="card">
          <p>VERSION: <code></code></p>
          <button id="btn-reload">Reload with Version</button>
          <button id="btn-clear">Clear Cache & Storage</button>
        </div>;
      document.getElementById('btn-reload').onclick = ()=>{
        const u = new URL(location.href); u.searchParams.set('v', K.VERSION); location.href = u.toString();
      };
      document.getElementById('btn-clear').onclick = async()=>{
        try{ for(const k of await caches.keys()) await caches.delete(k); }catch(e){}
        try{ localStorage.clear(); sessionStorage.clear(); }catch(e){}
        alert('Cache cleared. Reloading…'); location.reload();
      };
    }
  }

  function boot(){ driveSidebar(); window.addEventListener('hashchange', render); render(); }
  document.addEventListener('DOMContentLoaded', boot);
})();