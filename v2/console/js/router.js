(function(){
  const routes = new Set(['overview','analytics','projects','commands','settings']);
  function get(){
    const h=location.hash.replace('#/','').trim();
    return routes.has(h)? h : 'overview';
  }
  function go(tab){ if(routes.has(tab)) location.hash = #/; }
  window.KMSRoute = { get, go };
})();