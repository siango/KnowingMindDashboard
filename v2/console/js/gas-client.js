(function(){
  const K=(window.KMS||{});
  async function gasFetch(params={},method='GET'){
    const GAS_URL=(K.GAS_URL||'').trim(); if(!GAS_URL) throw new Error('GAS_URL not set');
    const headers={'User-Agent':'KMDashboard/1.0'}; if(K.API_KEY) headers['X-API-Key']=K.API_KEY;
    if(method==='GET'){
      const qs=new URLSearchParams(params).toString(); const url=qs? ${GAS_URL}?:GAS_URL;
      const res=await fetch(url,{headers}); if(!res.ok) throw new Error(GAS HTTP ); return await res.json();
    }else{
      headers['Content-Type']='application/json';
      const res=await fetch(GAS_URL,{method:'POST',headers,body:JSON.stringify(params)});
      if(!res.ok) throw new Error(GAS HTTP ); return await res.json();
    }
  }
  async function ping(url){ const r=await fetch(url,{cache:'no-store'}); return {ok:r.ok,status:r.status}; }
  window.KMSGAS={gasFetch,ping};
})();