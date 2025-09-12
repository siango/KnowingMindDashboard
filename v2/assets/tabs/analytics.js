export async function render(m){
  m.innerHTML = `
  <section class="card">
    <div class="section-hd">
      <h2 style="margin:0">Analytics</h2>
      <span class="badge badge-mock">MOCK</span>
      <span class="subtle">ยังไม่เชื่อมข้อมูลจริง</span>
    </div>
    <div class="card">
      <div class="section-hd"><b>สรุปสถานะ</b></div>
      <ul class="list">
        <li class="err">ยังไม่เชื่อม Traffic 7 วัน</li>
        <li class="err">ยังไม่เชื่อม Events by Type</li>
        <li class="err">ยังไม่เปิดใช้งานกราฟ (รอ API)</li>
      </ul>
    </div>
    <div class="card">
      <div class="section-hd"><b>ตัวอย่างข้อมูลจำลอง</b></div>
      <div class="kv">
        <div class="k">Top Event:</div><div class="v">View (420)</div>
        <div class="k">Second:</div><div class="v">Click (280)</div>
        <div class="k">Least:</div><div class="v">Signup (44)</div>
      </div>
    </div>
  </section>`;
}
