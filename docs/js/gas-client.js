(function(){
  const GAS_URL = (window.KMS && window.KMS.GAS_URL) || "";
  const API_KEY = (window.KMS && window.KMS.API_KEY) || "";

  async function gasFetch(params={}, method="GET", timeoutMs=12000){
    const controller = new AbortController();
    const t = setTimeout(()=>controller.abort(), timeoutMs);
    try{
      const headers = {"User-Agent":"KMDashboard/1.0"};
      if (API_KEY) headers["X-API-Key"] = API_KEY;

      if (method === "GET"){
        const qs  = new URLSearchParams(params).toString();
        const url = qs ? ${GAS_URL}??source=powershell&action=health : GAS_URL;
        const res = await fetch(url, {headers, signal: controller.signal});
        if (!res.ok) throw new Error(HTTP );
        return await res.json();
      } else {
        headers["Content-Type"] = "application/json";
        const res = await fetch(GAS_URL, {
          method: "POST",
          headers,
          body: JSON.stringify(params),
          signal: controller.signal
        });
        if (!res.ok) throw new Error(HTTP );
        return await res.json();
      }
    } catch(e){
      // JSONP fallback (เผื่อ CORS)
      console.warn("Fetch failed, try JSONP fallback:", e);
      return new Promise((resolve, reject)=>{
        const cb = KMS_JSONP__;
        window[cb] = (data)=>{ resolve(data); cleanup(); };
        const cleanup = ()=>{ delete window[cb]; if(s) s.remove(); clearTimeout(tt); };
        const url = new URL(GAS_URL);
        Object.entries(params||{}).forEach(([k,v])=>url.searchParams.set(k, v));
        url.searchParams.set("callback", cb);
        const s = document.createElement("script");
        s.src = url.toString();
        s.onerror = ()=>{ cleanup(); reject(new Error("JSONP load error")); };
        document.head.appendChild(s);
        const tt = setTimeout(()=>{ cleanup(); reject(new Error("JSONP timeout")); }, timeoutMs);
      });
    } finally { clearTimeout(t); }
  }

  async function loadDashboard(){
    try{
      const data = await gasFetch({ action: "dashboard" }, "GET");
      const el = document.querySelector('[data-km="dashboard-json"]');
      if (el) el.textContent = JSON.stringify(data, null, 2);
      // TODO: map ข้อมูลลงตารางจริงของหน้า
    }catch(e){
      const el = document.querySelector('[data-km="dashboard-json"]');
      if (el) el.textContent = "Load failed: " + e.message;
      console.error(e);
    }
  }

  window.KMS = window.KMS || {};
  window.KMS.gasFetch = gasFetch;
  window.KMS.loadDashboard = loadDashboard;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadDashboard);
  } else { loadDashboard(); }
})();
