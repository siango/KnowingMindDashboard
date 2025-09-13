;/* KM-OV-BOOT */
(function(){
  const DATA_BASE = './v2/console/data/';
  const sel=(q,d=document)=>d.querySelector(q);
  const html = String.raw;

  // mount point – use #content if exists, else body
  const host = sel('#content') || document.body;
  const root = document.createElement('div');
  root.className='km-wrap';
  root.innerHTML = html\
    <div class="km-head">
      <div class="km-title">Overview</div>
      <div class="km-lang">
        <button class="btn" data-lang="th">TH</button>
        <button class="btn active" data-lang="en">GB</button>
        <button class="btn" data-lang="zh">CN</button>
      </div>
    </div>
    <div id="km-grid" class="km-grid"></div>
  \;
  host.innerHTML=''; host.appendChild(root);

  const grid = sel('#km-grid',root);
  const badge = (s)=> s==='green'?'badge-ok':(s==='yellow'?'badge-warn':(s==='orange'?'badge-issue':'badge-unk'));

  function render(items){
    grid.innerHTML = items.map(it => html\
      <a class="km-card" href="./v2/console/#/\">
        <div class="top"><span>\</span><span class="badge \">\</span></div>
        <div class="km-sub">\</div>
      </a>\).join('');
  }

  fetch(DATA_BASE+'projects_index.json',{cache:'no-store'})
    .then(r=>r.json())
    .then(j=>render(j.items||[]))
    .catch(e=>{
      console.error(e);
      grid.innerHTML = '<div class="km-err">Load failed: '+String(e.message||e)+'</div>';
    });

  // lightweight language toggle (no reload; default EN/GB)
  let lang='en';
  root.querySelectorAll('.km-lang .btn').forEach(btn=>{
    btn.addEventListener('click',()=>{
      root.querySelectorAll('.km-lang .btn').forEach(x=>x.classList.remove('active'));
      btn.classList.add('active');
      lang = btn.dataset.lang || 'en';
      // (reserve for future static labels)
    });
  });
})();
