export async function render(mount){
  mount.innerHTML = <div class='card' style='padding:20px'>
    <h2>Analytics (mock)</h2>
    <p>Traffic chart will go here.</p>
  </div>;
  return ()=>{};
}
