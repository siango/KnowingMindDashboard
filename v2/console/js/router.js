(function(){
  const TOP=new Set(['overview','analytics','projects','commands','settings']);
  const PROJECTS=[
    {id:'kma',        title:'KnowingMindApp (KMA)'},
    {id:'arunroo',    title:'à¸­à¸£à¸¸à¸“à¸£à¸¹à¹‰ (ArunRoo)'},
    {id:'satishift',  title:'à¸ªà¸•à¸´à¹€à¸§à¸£ (SatiShift)'},
    {id:'ai-creator', title:'AI Creator'},
    {id:'moneyai',    title:'Beyond the Rich (MoneyAI)'},
    {id:'boonroo',    title:'BoonRoo (Donation)'},
    {id:'roblox',     title:'Roblox Mindful Life'},
    {id:'govdocs',    title:'Government-Docs (KMA)'},
    {id:'eboard',     title:'e-Board / Event Broadcasting'},
    {id:'lms',        title:'LMS / Meditation Courses'},
    {id:'dashboard',  title:'KnowingMind Dashboard'}
  ];
  function normalize(){
    const raw=location.hash.replace(/^#\/?/,'').trim();
    const ids=PROJECTS.map(p=>p.id);
    if(ids.includes(raw)){ history.replaceState(null,'','#/proj/'+raw); return {type:'project',id:raw}; }
    if(raw.startsWith('proj/')){ return {type:'project',id:raw.split('/')[1]||''}; }
    if(TOP.has(raw)) return {type:'top',tab:raw};
    return {type:'top',tab:'overview'};
  }
  function goTop(tab){ if(TOP.has(tab)) location.hash='#/'+tab; }
  function goProj(id){ location.hash='#/proj/'+id; }
  window.KMSRoute={normalize,goTop,goProj,PROJECTS};
})();

