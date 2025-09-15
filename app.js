const el = (h,cls,html)=>{ const x=document.createElement(h); if(cls) x.className=cls; if(html!=null) x.innerHTML=html; return x; }
async function fetchData(){ try{ const r=await fetch('data.json?v='+Date.now()); return await r.json(); }catch(e){ return null } }

function progressBar(p){ const wrap=el('div','progress'); const i=el('i'); i.style.width=(Math.max(0,Math.min(100,p)))+'%'; wrap.appendChild(i); return wrap; }
function barChartSVG(values, labels){
  const w=600,h=180,p=24; const max=Math.max(...values,1);
  let bars=''; const bw=(w-2*p)/values.length-12;
  values.forEach((v,i)=>{ const x=p+i*((w-2*p)/values.length); const bh=(h-2*p)*(v/max); const y=h-p-bh;
    bars+=\<rect x="\" y="\" width="\" height="\" rx="6" ry="6" fill="#80C2FF"></rect>
            <text x="\" y="\" text-anchor="middle" font-size="11" fill="#555">\</text>\; });
  return \<svg class="svgchart" viewBox="0 0 \ \">
    <line x1="\" y1="\" x2="\" y2="\" stroke="#ccc"/>
    <g>\</g>
  </svg>\;
}

function viewGeneral(){
  const root=el('div'); 
  root.appendChild(el('div','card',\<h3>นักธรรมตรี</h3>
    <div class="kv"><span>Offline exam</span><span class="ok">✓ ready</span></div>
    <div class="kv"><span>เฉลย/แบบฝึก</span><span>มีให้ใช้งาน</span></div>\));
  return root;
}

function viewAdmin(data){
  const root=el('div');
  const row=el('div','row'); root.appendChild(row);
  const proj=el('div','card'); proj.innerHTML='<h3>Project Progress</h3>'; 
  data.projects.forEach(p=>{
    const item=el('div'); item.innerHTML=\<div class="kv"><b>\</b><span>\%</span></div>\;
    item.appendChild(progressBar(p.progress)); proj.appendChild(item);
  });
  row.appendChild(proj);

  const val=el('div','card'); val.innerHTML='<h3>Valuation (Mock, M฿)</h3>';
  const labels=data.projects.map(p=>p.name);
  const vals=data.projects.map(p=>Number(p.value_m));
  val.innerHTML += barChartSVG(vals, labels);
  row.appendChild(val);

  const kpi=el('div','card'); 
  kpi.innerHTML=\<h3>KPIs</h3>
    <div class="kv"><span>DAU</span><b>\</b></div>
    <div class="kv"><span>WAU</span><b>\</b></div>
    <div class="kv"><span>7-day Retention</span><b>\%</b></div>
    <div class="kv"><span>Completion</span><b>\%</b></div>
    <div class="kv"><span>NPS</span><b>\</b></div>\;
  row.appendChild(kpi);

  return root;
}

function viewDev(data){
  const root=el('div');
  root.appendChild(el('div','card',\<h3>Dev / Observer</h3>
    <div class="kv"><span>Deploys today</span><b>\</b></div>
    <div class="kv"><span>Errors today</span><b class="\">\</b></div>
    <div class="small">* Logs/Errors/Secrets = private (ไม่เปิดสาธารณะ)</div>\));
  return root;
}

function viewAnalytics(data){
  const root=el('div');
  const row=el('div','row'); root.appendChild(row);
  const comp=el('div','card'); comp.innerHTML='<h3>Completion</h3>'; comp.appendChild(progressBar(data.analytics.completion)); row.appendChild(comp);
  const ret=el('div','card'); ret.innerHTML='<h3>7-day Retention</h3>'; ret.appendChild(progressBar(data.analytics.retention7)); row.appendChild(ret);
  const dau=el('div','card'); dau.innerHTML='<h3>DAU vs WAU</h3>'; dau.innerHTML+=barChartSVG([data.analytics.dau,data.analytics.wau],['DAU','WAU']); row.appendChild(dau);
  return root;
}

function viewChecklist(data){
  const root=el('div');
  const card=el('div','card'); card.innerHTML='<h3>Checklist วันนี้</h3>';
  data.checklist_today.forEach(i=>{
    const line=el('div','kv'); 
    line.innerHTML=\<span>\ \</span><span class="\">\</span>\;
    card.appendChild(line);
  });
  root.appendChild(card);
  return root;
}

async function render(route){
  const host=document.getElementById('app'); host.innerHTML='Loading...';
  const data=await fetchData()||{projects:[],analytics:{},checklist_today:[]};

  if(route==='#/general')      host.replaceChildren(viewGeneral());
  else if(route==='#/admin')   host.replaceChildren(viewAdmin(data));
  else if(route==='#/dev')     host.replaceChildren(viewDev(data));
  else if(route==='#/analytics') host.replaceChildren(viewAnalytics(data));
  else if(route==='#/checklist') host.replaceChildren(viewChecklist(data));
  else                         host.replaceChildren(viewGeneral());
}
addEventListener('hashchange',()=>render(location.hash));
render(location.hash||'#/general');
