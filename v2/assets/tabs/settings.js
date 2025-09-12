export async function render(m){
  const base = (localStorage.getItem("kms_api_base")||"");
  m.innerHTML = `
  <section class="card">
    <div class="section-hd">
      <h2 style="margin:0">Settings</h2>
      <span class="badge ${base ? 'badge-live' : 'badge-mock'}">${base ? 'LIVE' : 'MOCK'}</span>
      <span class="subtle">ตั้งค่า API base เพื่อเปลี่ยนจาก mock → live</span>
    </div>
    <div class="card">
      <div class="kv" style="align-items:center">
        <div class="k">API Base</div>
        <div class="v"><input id="api" class="search" style="width:100%" placeholder="https://your.api/kms" value="${base}"></div>
        <div class="k">การใช้งาน</div>
        <div class="v subtle">ใส่ API base แล้วกด Save → รีเฟรชหน้า จากนั้นแต่ละแท็บจะโหลดข้อมูลจริงได้</div>
      </div>
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
        <button id="save" class="tab">Save</button>
        <button id="clear" class="tab">Clear</button>
        <span id="status" class="subtle"></span>
      </div>
    </div>
    <div class="card">
      <div class="section-hd"><b>สถานะปัจจุบัน</b></div>
      <ul class="list">
        <li class="${base ? 'ok' : 'err'}">Base URL ${base ? 'พร้อมใช้งาน' : 'ยังไม่ตั้งค่า'}</li>
        <li class="err">Overview: KPIs ยังเป็นค่า mock</li>
        <li class="err">Analytics: กราฟยังเป็น mock</li>
        <li class="err">Reports: รายการบางส่วนยังเป็น mock</li>
      </ul>
    </div>
  </section>`;
  document.getElementById('save').onclick = ()=>{
    const v = (document.getElementById('api').value||'').trim();
    localStorage.setItem('kms_api_base', v);
    document.getElementById('status').textContent = 'Saved. กดรีเฟรชหน้าเพื่อให้แท็บเปลี่ยนสถานะ';
  };
  document.getElementById('clear').onclick = ()=>{
    localStorage.removeItem('kms_api_base');
    document.getElementById('status').textContent = 'Cleared. กดรีเฟรชหน้า';
  };
}
