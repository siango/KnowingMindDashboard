export async function render(mount){
  mount.innerHTML=<div class='card' style='padding:20px'>
    <div class='card-title'>Settings</div>
    <label class='muted'>API Endpoint (ใช้กับแท็บ Overview)</label>
    <input id='api' class='search' style='width:100%' placeholder='https://your.api/endpoint'/>
    <p class='muted' style='margin-top:10px'>* เก็บใน localStorage ของเบราว์เซอร์</p>
  </div>;
  const key='kms_api_endpoint', el=document.getElementById('api');
  el.value=localStorage.getItem(key)||''; el.addEventListener('change',()=>localStorage.setItem(key,el.value));
}
