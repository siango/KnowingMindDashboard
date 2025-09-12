export async function render(mount){
  mount.innerHTML=<div class='card' style='padding:20px'>
    <div class='card-title'>Reports</div>
    <ul id='files' class='muted'></ul></div>;
  const list=[{name:'Weekly Summary',href:'./reports/weekly-sample.pdf'},{name:'OKR Q4 Plan',href:'./reports/okr-q4.pdf'}];
  const ul=document.getElementById('files'); ul.innerHTML=list.map(x=><li><a href='\' target='_blank'>\</a></li>).join('');
}
