export async function render(mount){
  mount.innerHTML = <div class='card' style='padding:20px'>
    <h2>Overview (mock)</h2>
    <p>Active Users: 123</p>
    <p>Sessions: 456</p>
    <p>Conversion: 7.8%</p>
    <p>Errors: 0</p>
  </div>;
  return ()=>{};
}
