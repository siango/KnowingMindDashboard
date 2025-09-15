const app = document.getElementById('app');
function render(route){
  if(route==="#/general"){
    app.innerHTML = "<h2>นักธรรมตรี</h2><ul><li class='ok'>✓ Offline exam</li><li>เฉลย</li></ul>";
  } else if(route==="#/admin"){
    app.innerHTML = "<h2>ผู้บริหาร</h2><ul><li class='ok'>✓ มูลค่าโครงการ</li><li class='err'>✗ เช็คลิสต์ (mock error)</li></ul>";
  } else if(route==="#/dev"){
    app.innerHTML = "<h2>นักพัฒนา</h2><ul><li class='ok'>✓ Heartbeats</li><li class='err'>✗ Error log</li></ul>";
  } else {
    app.innerHTML = "<h2>Welcome KnowingMind Dashboard</h2>";
  }
}
window.addEventListener("hashchange", ()=>render(location.hash));
render(location.hash || "#/general");
