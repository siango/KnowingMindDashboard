export async function fetchJSON(url,{timeout=8000,headers={}}={}) {
  const ctrl=new AbortController(); const t=setTimeout(()=>ctrl.abort(),timeout);
  try{
    const res=await fetch(url,{headers:{'Accept':'application/json',...headers},signal:ctrl.signal});
    clearTimeout(t); if(!res.ok) return {ok:false,error:HTTP }; return {ok:true,data:await res.json()};
  }catch(e){
    clearTimeout(t);
    try{ const r2=await fetch(url,{headers:{'Accept':'application/json',...headers}}); if(!r2.ok) return {ok:false,error:HTTP }; return {ok:true,data:await r2.json()}; }
    catch(e2){ return {ok:false,error:e2.message||'network'} }
  }
}
export const getBase=()=> (localStorage.getItem('kms_api_base')||'').replace(/\/+$/,'');
export const fmt=n=> (typeof n==='number'? n.toLocaleString('en-US'):n);
