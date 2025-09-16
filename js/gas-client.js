// Minimal stub: ถ้า API_BASE ว่าง จะคืน mock ให้อัตโนมัติ
(function(){
  const cfg = (window.KMDASH_CONFIG||{API_BASE:"",API_KEY:""});
  function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }
  async function go(ep){
    if(!cfg.API_BASE){ await sleep(100); return { ok:true, json: async()=>({status:'offline', ep, t:new Date().toISOString()})}; }
    try{
      const r = await fetch(cfg.API_BASE.replace(/\/$/,'')+ep, { headers: cfg.API_KEY?{'x-api-key':cfg.API_KEY}:{}}); 
      return r;
    }catch(e){ await sleep(100); return { ok:true, json: async()=>({status:'degraded', ep, err:String(e)})}; }
  }
  window.KMDASH = {
    fetchOverview:  ()=>go('/dashboard/overview').then(r=>r.json()),
    fetchAnalytics: ()=>go('/dashboard/analytics').then(r=>r.json()),
    fetchChecklist: ()=>go('/dashboard/checklist').then(r=>r.json()),
  };
})();
