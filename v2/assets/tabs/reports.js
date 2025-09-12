export async function render(mount){
  mount.innerHTML = <div class='card' style='padding:20px'>
    <h2>Reports (mock)</h2>
    <ul><li>Weekly Summary</li><li>OKR Q4 Plan</li></ul>
  </div>;
  return ()=>{};
}
