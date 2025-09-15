# OSP9 — Dhamma-Tri Offline (MCQ + Essay + Export/Import)  REV-20250915 (PS 5.1 Compatible)
param(
  [string]$Owner    = "siango",
  [string]$Repo     = "KnowingMindDashboard",
  [string]$Branch   = "gh-pages",
  # เปลี่ยนเป็น custom domain ได้ เช่น https://knowingmindproject.website/v2/console
  [string]$DashBase = "https://siango.github.io/KnowingMindDashboard/v2/console"
)

$ErrorActionPreference = 'Stop'
function Info($m){ Write-Host "[i] $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "[✓] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Die($m){ Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

# --- เตรียมที่ทำงาน (clone gh-pages) ---
git --version | Out-Null
$repoUrl = "https://github.com/$Owner/$Repo.git"
$work = Join-Path $env:TEMP ("kms_tri_essay_{0}" -f ([guid]::NewGuid().ToString("N")))
Info "Cloning $Owner/$Repo ($Branch) -> $work"
git clone --depth 1 --branch $Branch $repoUrl $work | Out-Null

# --- โฟลเดอร์ offline viewer ---
$offlineRoot = Join-Path $work "v2/console/assets/offline/dhamma-tri/offline_exam"
New-Item -ItemType Directory -Force -Path $offlineRoot | Out-Null
$dataDir = Join-Path $offlineRoot "data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

# --- index.html (offline viewer: MCQ + Essay + Export/Import + autosave) ---
$idx = @"
<!doctype html>
<html lang="th"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Dhamma-Tri Offline Exam (MCQ + Essay)</title>
<style>
  body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;margin:14px;background:#fafafa;color:#222}
  header{display:flex;gap:8px;align-items:center;justify-content:space-between;margin-bottom:10px}
  .btn{border:1px solid #ddd;background:#fff;border-radius:10px;padding:8px 12px;cursor:pointer}
  .wrap{display:grid;gap:10px}
  .q{border:1px solid #eee;border-radius:12px;padding:12px;background:#fff}
  .q h3{margin:0 0 6px 0;font-size:16px}
  .meta{font-size:12px;color:#666;margin-bottom:6px}
  .opt{margin:6px 0;display:block}
  textarea{width:100%;min-height:110px;border:1px solid #ddd;border-radius:10px;padding:10px;font-family:inherit}
  .toolbar{position:sticky;bottom:12px;display:flex;gap:8px;justify-content:flex-end;margin-top:10px}
  .score{font-weight:600}
  .pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef;color:#225;}
  .hint{color:#666;font-size:12px}
</style>
</head>
<body>
<header>
  <div>
    <strong>นักธรรมชั้นตรี (ออฟไลน์)</strong>
    <span class="pill" id="mode">OFFLINE</span>
  </div>
  <div>
    <button class="btn" id="btnLoadMcq">ชุดปรนัย</button>
    <button class="btn" id="btnLoadEssay">ชุดอัตนัย</button>
  </div>
</header>

<div class="wrap">
  <div class="q">
    <div class="meta">โหมดออฟไลน์ — คำตอบเก็บไว้ในอุปกรณ์นี้เท่านั้น</div>
    <div class="hint">สลับชุดข้อสอบได้ทุกเมื่อ: ปรนัย/อัตนัย · ระบบจะบันทึกคำตอบอัตโนมัติ</div>
  </div>
  <div id="list"></div>
</div>

<div class="toolbar">
  <span class="score" id="scoreBox">คะแนน: 0/0</span>
  <button class="btn" id="btnSum">สรุปคะแนน</button>
  <button class="btn" id="btnSave">บันทึก</button>
  <button class="btn" id="btnExport">ส่งออก (.json)</button>
  <input type="file" id="fileImport" style="display:none" accept=".json"/>
  <button class="btn" id="btnImport">นำเข้า</button>
  <button class="btn" id="btnClear">ล้างคำตอบ</button>
</div>

<script>
(function(){
  const KEY = 'dhamma_tri_offline_answers_v1';
  const STATE = { answers:{}, setId:'mcq' }; // mcq | essay
  const els = {
    list: document.getElementById('list'),
    sum: document.getElementById('btnSum'),
    save: document.getElementById('btnSave'),
    export: document.getElementById('btnExport'),
    importBtn: document.getElementById('btnImport'),
    fileImport: document.getElementById('fileImport'),
    clear: document.getElementById('btnClear'),
    score: document.getElementById('scoreBox'),
    btnMcq: document.getElementById('btnLoadMcq'),
    btnEssay: document.getElementById('btnLoadEssay')
  };

  function lsLoad(){
    try{
      const raw = localStorage.getItem(KEY);
      if(!raw) return;
      const o = JSON.parse(raw);
      if(o && o.answers) STATE.answers = o.answers;
    }catch(e){}
  }
  function lsSave(){
    localStorage.setItem(KEY, JSON.stringify({answers:STATE.answers, ts:Date.now()}));
  }
  function setAnswer(qid, value){
    STATE.answers[qid] = value;
    lsSave();
  }

  async function loadSet(setId){
    STATE.setId = setId;
    let file = 'data/questions_sample.json';
    if(setId==='essay') file = 'data/questions_essay_sample.json';
    const res = await fetch(file+'?v='+Date.now());
    const data = await res.json();
    render(data);
  }

  function render(data){
    els.list.innerHTML = '';
    let correct=0, total=0;
    (data.questions||[]).forEach((q, i)=>{
      total++;
      const qid = (data.id || 'set') + '_' + (q.id || ('Q'+(i+1)));
      const div = document.createElement('div');
      div.className='q';
      const head = document.createElement('h3');
      head.textContent = 'ข้อ ' + (i+1) + '. ' + q.text;
      div.appendChild(head);

      if(q.type==='essay'){
        const ta = document.createElement('textarea');
        ta.placeholder = q.placeholder || 'พิมพ์คำตอบที่นี่...';
        ta.value = (STATE.answers[qid] || '');
        ta.oninput = ()=> setAnswer(qid, ta.value);
        div.appendChild(ta);

        if(q.guideline){
          const hint = document.createElement('div');
          hint.className = 'hint';
          hint.textContent = 'แนวตอบ: ' + (Array.isArray(q.guideline)? q.guideline.join(' / ') : q.guideline);
          div.appendChild(hint);
        }
      } else {
        // MCQ
        const box = document.createElement('div');
        (q.options||[]).forEach((opt, idx)=>{
          const id = qid + '_' + idx;
          const label = document.createElement('label');
          label.className='opt';
          const input = document.createElement('input');
          input.type='radio';
          input.name=qid;
          input.value=idx;
          input.checked = (String(STATE.answers[qid])===String(idx));
          input.onchange = ()=> setAnswer(qid, Number(idx));
          label.appendChild(input);
          label.appendChild(document.createTextNode(' '+opt));
          box.appendChild(label);
        });
        div.appendChild(box);
        if(typeof q.answer === 'number' && String(STATE.answers[qid])===String(q.answer)){ correct++; }
      }

      els.list.appendChild(div);
    });

    els.score.textContent = 'คะแนน: '+correct+'/'+total+(STATE.setId==='essay'?' (อัตนัยไม่ตรวจอัตโนมัติ)':'');
  }

  function summary(){
    // ตรวจเฉพาะ MCQ เท่านั้น
    if(STATE.setId==='essay'){ alert('ชุดอัตนัย: ระบบไม่ตรวจอัตโนมัติ'); return; }
    const checked = els.score.textContent;
    alert(checked);
  }

  function exportJson(){
    const payload = { ts: new Date().toISOString(), set: STATE.setId, answers: STATE.answers };
    const blob = new Blob([JSON.stringify(payload,null,2)], {type:'application/json'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'dhamma_tri_answers_'+Date.now()+'.json';
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function importJson(){
    els.fileImport.click();
  }
  els.fileImport.addEventListener('change', function(){
    const f = this.files && this.files[0];
    if(!f) return;
    const r = new FileReader();
    r.onload = function(){
      try{
        const o = JSON.parse(r.result);
        if(o.answers){ STATE.answers = o.answers; lsSave(); loadSet(STATE.setId); alert('นำเข้าคำตอบสำเร็จ'); }
        else alert('ไฟล์ไม่ถูกต้อง');
      }catch(e){ alert('อ่านไฟล์ไม่สำเร็จ'); }
    };
    r.readAsText(f);
    this.value = '';
  });

  function clearAns(){
    if(confirm('ล้างคำตอบทั้งหมดในเครื่องนี้?')){ STATE.answers={}; lsSave(); loadSet(STATE.setId); }
  }

  // bindings
  els.sum.onclick = summary;
  els.save.onclick = lsSave;
  els.export.onclick = exportJson;
  els.importBtn.onclick = importJson;
  els.clear.onclick = clearAns;
  document.getElementById('btnLoadMcq').onclick = ()=> loadSet('mcq');
  document.getElementById('btnLoadEssay').onclick = ()=> loadSet('essay');

  // boot
  lsLoad();
  loadSet('mcq');
})();
</script>
</body></html>
"@

Set-Content -Encoding UTF8 -Path (Join-Path $offlineRoot "index.html") -Value $idx

# --- MCQ sample (คงไว้ใช้ได้ทันที) ---
@"
{
  "id": "set_mcq_sample",
  "title": "Dhamma-Tri Sample MCQ",
  "questions": [
    { "id":"Q1", "type":"mcq", "text":"พุทธคุณ หมายถึงข้อใด", "options": ["คุณของพระพุทธเจ้า","คุณของพระวินัย","คุณของพระธรรม","คุณของพระสงฆ์"], "answer": 0 },
    { "id":"Q2", "type":"mcq", "text":"ไตรสิกขา มีอะไรบ้าง", "options": ["ศีล สมาธิ ปัญญา","ทาน ศีล ภาวนา","ขันธ์ ธาตุ อายตนะ","มรรค ผล นิพพาน"], "answer": 0 },
    { "id":"Q3", "type":"mcq", "text":"อริยสัจ 4 ข้อใดคือข้อที่ต้องละ", "options": ["ทุกข์","สมุทัย","นิโรธ","มรรค"], "answer": 1 }
  ]
}
"@ | Out-File -Encoding UTF8 (Join-Path $dataDir "questions_sample.json")

# --- Essay sample (ใหม่) ---
@"
{
  "id": "set_essay_sample",
  "title": "Dhamma-Tri Sample Essay",
  "questions": [
    {
      "id":"E01",
      "type":"essay",
      "text":"อธิบายความหมายของ พุทธคุณ ธรรมคุณ สังฆคุณ ให้ยกอย่างน้อยหมวดละ 1 ประเด็น",
      "guideline": ["คุณของพระพุทธเจ้า","คุณของพระธรรม","คุณของพระสงฆ์"],
      "placeholder": "ตอบอธิบายแต่ละหมวดอย่างย่อ"
    },
    {
      "id":"E02",
      "type":"essay",
      "text":"ไตรสิกขา (ศีล สมาธิ ปัญญา) เกี่ยวข้องกับการปฏิบัติประจำวันของผู้เรียนอย่างไร",
      "guideline": "กล่าวถึงตัวอย่างปฏิบัติของตนเองอย่างน้อย 2 สถานการณ์",
      "placeholder": "เล่าประสบการณ์จริง + สิ่งที่เรียนรู้"
    }
  ]
}
"@ | Out-File -Encoding UTF8 (Join-Path $dataDir "questions_essay_sample.json")

# --- อัปเดตแท็บ Dhamma-Tri ในแดชบอร์ดให้ชี้ viewer ---
$dtriPath = Join-Path $work "v2/console/tabs/dhamma-tri.html"
if(-not (Test-Path $dtriPath)){
  New-Item -ItemType File -Path $dtriPath | Out-Null
}
$ts = Get-Date -Format "yyyyMMddHHmmss"
@"
<h2>นักธรรมชั้นตรี <small>(OFFLINE VIEWER)</small></h2>
<p>คืนนี้รองรับทั้ง <b>ข้อสอบปรนัย</b> และ <b>อัตนัย</b> แบบออฟไลน์ในเครื่อง:</p>
<ul>
  <li><a href="../assets/offline/dhamma-tri/offline_exam/index.html?v=$ts" target="_blank">เปิด Offline Viewer</a></li>
  <li><a href="../assets/offline/dhamma-tri_pack.zip?v=$ts">ดาวน์โหลดแพ็กออฟไลน์ (.zip)</a></li>
</ul>
<p class="hint">หมายเหตุ: โหมดออนไลน์/ตรวจอัตนัย จะเชื่อมพรุ่งนี้ — คำตอบที่พิมพ์คืนนี้จะถูกเก็บไว้ในเครื่อง (localStorage) และสามารถ <b>ส่งออกไฟล์ .json</b> เพื่อนำเข้าใหม่ได้</p>
"@ | Out-File -Encoding UTF8 $dtriPath

# --- อัปเดตหน้า index ของแดชบอร์ดให้มีปุ่ม Refresh/3 กลุ่ม (ถ้ายังไม่มี) ---
$consoleIdx = Join-Path $work "v2/console/index.html"
if(-not (Test-Path $consoleIdx)){
  New-Item -ItemType File -Path $consoleIdx | Out-Null
}
$content = Get-Content $consoleIdx -Raw
if($content -notmatch "#/dhamma-tri"){
  $content = $content -replace "</header>","</header>`n<section class=""rolebar""><a href=""#/dhamma-tri"" class=""card"">นักธรรมชั้นตรี<br><small>ข้อสอบออฟไลน์</small></a> <a href=""#/overview"" class=""card"">ผู้บริหาร</a> <a href=""#/projects"" class=""card"">นักพัฒนา</a></section>"
  Set-Content -Encoding UTF8 -Path $consoleIdx -Value $content
  Warn "Injected 3-entrance rolebar into console index"
}

# --- สร้าง ZIP แพ็กออฟไลน์ (.zip) ---
$zipRoot = Join-Path $work "v2/console/assets/offline/dhamma-tri_pack.zip"
if(Test-Path $zipRoot){ Remove-Item -Force $zipRoot }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory((Split-Path $offlineRoot -Parent), $zipRoot)

# --- Commit + Push ---
Push-Location $work
git add -A
git commit -m ("feat(dhamma-tri): offline viewer MCQ+Essay + export/import (rev {0})" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) | Out-Null
if($env:GITHUB_TOKEN){
  $remote = "https://$($env:GITHUB_TOKEN)@github.com/$Owner/$Repo.git"
  git push $remote $Branch | Out-Null
  Ok "Pushed to $Owner/$Repo:$Branch"
} else {
  Warn "ไม่มี GITHUB_TOKEN — ไฟล์สร้างครบแล้วในเครื่อง (ยังไม่ push)"
  Write-Host "ตรวจไฟล์และ push เอง: cd `"$work`" ; git push origin $Branch" -ForegroundColor DarkGray
}
Pop-Location

Ok ("เสร็จสิ้น — เปิดใช้งาน: {0}/?v={1}#/dhamma-tri" -f $DashBase, (Get-Date -Format "yyyyMMddHHmmss"))
