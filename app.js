const el=(h,cls,html)=>{const x=document.createElement(h);if(cls)x.className=cls;if(html!=null)x.innerHTML=html;return x;}
async function fetchData(){try{const r=await fetch("data.json?v="+Date.now());return await r.json()}catch(e){return null}}

function progressBar(p){const w=el("div","progress");const i=el("i");i.style.width=(Math.max(0,Math.min(100,p)))+"%";w.appendChild(i);return w}

function viewGeneral(){const r=el("div");r.appendChild(el("div","card","<h3>นักธรรมตรี</h3><div class='kv'><span>Offline exam</span><span class='ok'>✓ ready</span></div>"));return r}

function viewAdmin(d){const r=el("div"),row=el("div","row");r.appendChild(row);const proj=el("div","card");proj.innerHTML="<h3>Project Progress</h3>";
d.projects.forEach(p=>{const item=el("div");item.innerHTML=`<div class='kv'><b>${p.name}</b><span>${p.progress}%</span></div>`;item.appendChild(progressBar(p.progress));proj.appendChild(item)});row.appendChild(proj);return r}

function viewDev(){const r=el("div");r.appendChild(el("div","card","<h3>Dev / Observer</h3><div class='kv'><span>Deploys today</span><b>0</b></div><div class='kv'><span>Errors today</span><b class='err'>0</b></div>"));return r}

function viewAnalytics(d){const r=el("div"),row=el("div","row");r.appendChild(row);const comp=el("div","card");comp.innerHTML="<h3>Completion</h3>";comp.appendChild(progressBar(d.analytics.completion||0));row.appendChild(comp);return r}

function viewChecklist(d){const r=el("div"),c=el("div","card");c.innerHTML="<h3>Checklist วันนี้</h3>";d.checklist_today.forEach(i=>{const line=el("div","kv");line.innerHTML=`<span>${i.task}</span><span>${i.status}</span>`;c.appendChild(line)});r.appendChild(c);return r}

async function render(route){const host=document.getElementById("app");host.textContent="Loading...";const d=await fetchData()||{projects:[],analytics:{},checklist_today:[]};let view=viewGeneral();
if(route==="#/admin")view=viewAdmin(d);else if(route==="#/dev")view=viewDev(d);else if(route==="#/analytics")view=viewAnalytics(d);else if(route==="#/checklist")view=viewChecklist(d);host.replaceChildren(view)}
addEventListener("hashchange",()=>render(location.hash));render(location.hash||"#/general");
