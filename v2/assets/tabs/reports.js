export async function render(m){
  m.innerHTML = `
  <section class="card">
    <div class="section-hd">
      <h2 style="margin:0">Reports</h2>
      <span class="badge badge-mock">MOCK</span>
      <span class="subtle">รายการรายงานล่าสุด (ตัวอย่าง)</span>
    </div>
    <div class="card">
      <div class="section-hd"><b>Recent Items</b></div>
      <ul class="list">
        <li class="ok">Weekly Summary (PDF) — 13/09 10:20</li>
        <li class="ok">OKR Q4 Plan (PDF) — 12/09 16:05</li>
        <li class="err">System Health (CSV) — รอเชื่อม API</li>
      </ul>
    </div>
  </section>`;
}
