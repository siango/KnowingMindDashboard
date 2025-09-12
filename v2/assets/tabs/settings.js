export async function render(mount){
  mount.innerHTML = <div class='card' style='padding:20px'>
    <h2>Settings</h2>
    <p>API Base (จะทำฟอร์มจริงทีหลัง)</p>
  </div>;
  return ()=>{};
}
