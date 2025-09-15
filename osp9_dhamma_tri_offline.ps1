# OSP9 — Dhamma-Tri Offline Tonight (PS 5.1 Compatible)  REV-20250915
param(
  [string]$Owner    = "siango",
  [string]$Repo     = "KnowingMindDashboard",
  [string]$Branch   = "gh-pages",
  # ถ้าใช้โดเมน custom ให้เปลี่ยนเป็น https://knowingmindproject.website/v2/console
  [string]$DashBase = "https://siango.github.io/KnowingMindDashboard/v2/console"
)

$ErrorActionPreference = 'Stop'
function Info($m){ Write-Host "[i] $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "[✓] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Die($m){ Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

# --- เตรียมโฟลเดอร์ทำงาน (clone gh-pages) ---
$repoUrl = "https://github.com/$Owner/$Repo.git"
$work = Join-Path $env:TEMP ("kms_tri_{0}" -f ([guid]::NewGuid().ToString("N")))

git --version | Out-Null
Info "Cloning $Owner/$Repo ($Branch) -> $work"
git clone --depth 1 --branch $Branch $repoUrl $work | Out-Null

# --- สร้างโครงไฟล์ขั้นต่ำถ้ายังไม่มี ---
$paths = @(
  "index.html",
  "v2/console/index.html",
  "v2/console/js/app.js",
  "v2/console/js/router.js",
  "v2/console/config.env.js",
  "v2/console/tabs/overview.html",
  "v2/console/tabs/projects.html",
  "v2/console/tabs/dhamma-tri.html",
  ".nojekyll"
)
foreach($p in $paths){
  $full = Join-Path $work $p
  $dir  = Split-Path $full -Parent
  if(-not (Test-Path $dir)){ New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  if(-not (Test-Path $full)){
    if($p -like "*.html"){
      "<!doctype html><meta charset='utf-8'><title>$p</title><div id='app' style='padding:16px;font-family:system-ui'>[$p]</div>" | Out-File -Encoding UTF8 $full
      Warn "Created placeholder: $p"
    } elseif($p -like "*.js"){
      "/* $p placeholder */" | Out-File -Encoding UTF8 $full
      Warn "Created placeholder: $p"
    } else {
      "" | Out-File -Encoding UTF8 $full
      Warn "Created: $p"
    }
  } else {
    Ok "Found: $p"
  }
}

# --- อัปเดต index.html (root redirect ไป console + มีปุ่มทางเข้า 3 กลุ่ม) ---
$rootIdx = Join-Path $work "index.html"
@"
<!doctype html>
<html lang="th"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>KnowingMind Dashboard</title>
<script>location.href="v2/console/?v="+Date.now()+"#/overview";</script>
</head><body>Redirecting...</body></html>
"@ | Out-File -Encoding UTF8 $rootIdx

# --- อัปเดต v2/console/index.html (ปุ่ม 3 กลุ่ม + Refresh + container #app) ---
$consoleIdx = Join-Path $work "v2/console/index.html"
@"
<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>KnowingMind Dashboard v2</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#fafafa;color:#222}
  header{display:flex;gap:8px;align-items:center;justify-content:space-between;padding:10px 14px;background:#fff;border-bottom:1px solid #eee;position:sticky;top:0}
  .rolebar{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));padding:12px}
  .card{display:block;background:#fff;border:1px solid #eee;border-radius:16px;padding:16px;text-decoration:none;color:#111;box-shadow:0 1px 2px rgba(0,0,0,.04)}
  .card small{color:#666}
  main{padding:12px}
  .btn{border:1px solid #ddd;background:#fff;border-radius:10px;padding:8px 12px;cursor:pointer}
  #app{margin-top:8px}
</style>
<script src="config.env.js?v=__TS__"></script>
<script src="js/router.js?v=__TS__"></script>
</head>
<body>
<header>
  <div><strong>KnowingMind Dashboard</strong></div>
  <div>
    <button class="btn" onclick="location.href=location.pathname+'?v='+Date.now()+'#/overview'">Refresh</button>
  </div>
</header>

<section class="rolebar">
  <a href="#/dhamma-tri" class="card">นักธรรมชั้นตรี<br><small>ข้อสอบออฟไลน์ (คืนนี้) / ออนไลน์ (พรุ่งนี้)</small></a>
  <a href="#/overview" class="card">ผู้บริหาร<br><small>ภาพรวม 11 โครงการ / มูลค่า / แผน</small></a>
  <a href="#/projects" class="card">นักพัฒนา<br><small>Projects / Logs / Scripts</small></a>
</section>

<main id="app">Loading...</main>
</body>
</html>
"@.Replace("__TS__", [string](Get-Date -Format "yyyyMMddHHmmss")) | Out-File -Encoding UTF8 $consoleIdx

# --- อัปเดต router.js (routes + alias + loader) ---
$routerJs = Join-Path $work "v2/console/js/router.js"
@"
(function(){
  var routes = {
    '#/overview': 'tabs/overview.html',
    '#/projects': 'tabs/projects.html',
    '#/analytics': 'tabs/analytics.html',
    '#/checklist': 'tabs/checklist.html',
    '#/dhamma-tri': 'tabs/dhamma-tri.html'
  };
  var aliases = {
    '#/overview2': '#/overview',
    '#/project-list': '#/projects'
  };
  function currentRoute(){
    var h = location.hash || '#/overview';
    return aliases[h] || h;
  }
  async function load(){
    var r = currentRoute();
    var file = routes[r];
    if(!file){ location.hash = '#/overview'; return; }
    try{
      var res = await fetch(file + '?v=' + Date.now());
      var html = await res.text();
      document.getElementById('app').innerHTML = html;
    }catch(e){
      document.getElementById('app').innerHTML = '<p>โหลดไม่สำเร็จ</p>';
    }
  }
  window.addEventListener('hashchange', load);
  window.addEventListener('DOMContentLoaded', load);
})();
"@ | Out-File -Encoding UTF8 $routerJs

# --- config.env.js (ตั้ง MOCK_MODE=true, ปลอดภัยคืนนี้) ---
$config = Join-Path $work "v2/console/config.env.js"
@"
window.KMS = {
  GAS_URL: "",
  LIVE_KMA: false,
  MOCK_MODE: true
};
"@ | Out-File -Encoding UTF8 $config

# --- แท็บ Dhamma-Tri (ชี้ไป offline viewer + ปุ่มดาวน์โหลด ZIP) ---
$dtri = Join-Path $work "v2/console/tabs/dhamma-tri.html"
@"
<h2>นักธรรมชั้นตรี <small id="mode"></small></h2>
<p>คืนนี้พร้อมใช้งานแบบ <b>ข้อสอบออฟไลน์</b> (ดาวน์โหลดด้านล่าง) — โหมดออนไลน์จะเปิดใช้งานพรุ่งนี้</p>
<ul>
  <li><a href="../assets/offline/dhamma-tri/offline_exam/index.html?v=__TS__" target="_blank">เปิดดูตัวอย่างข้อสอบออฟไลน์ (ในเบราว์เซอร์)</a></li>
  <li><a href="../assets/offline/dhamma-tri_pack.zip?v=__TS__">ดาวน์โหลดแพ็กข้อสอบออฟไลน์ (.zip)</a></li>
</ul>
<hr/>
<div>
  <h3>วิธีใช้ (ออฟไลน์)</h3>
  <ol>
    <li>ดาวน์โหลดไฟล์ ZIP แล้วแตกไฟล์</li>
    <li>เปิดไฟล์ <code>offline_exam/index.html</code> ด้วยเบราว์เซอร์ (ไม่ต้องต่อเน็ต)</li>
    <li>ทำข้อสอบ → กดสรุปคะแนน (คะแนนอยู่ในเครื่องผู้ใช้)</li>
  </ol>
</div>
<script>
  (function(){
    var m = (window.KMS && window.KMS.MOCK_MODE) ? 'MOCK MODE' : 'LIVE';
    document.getElementById('mode').textContent = '('+m+')';
  })();
</script>
"@.Replace("__TS__", [string](Get-Date -Format "yyyyMMddHHmmss")) | Out-File -Encoding UTF8 $dtri

# --- สร้าง offline viewer + ตัวอย่างข้อสอบ ---
$offlineRoot = Join-Path $work "v2/console/assets/offline/dhamma-tri/offline_exam"
New-Item -ItemType Directory -Force -Path $offlineRoot | Out-Null

# index.html (offline viewer)
@"
<!doctype html>
<html lang="th"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Dhamma-Tri Offline Exam</title>
<style>
 body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;margin:14px}
 .q{border:1px solid #eee;border-radius:12px;padding:12px;margin-bottom:10px;background:#fff}
 .opt{margin:6px 0}
 .score{position:fixed;right:16px;bottom:16px}
 .btn{border:1px solid #ddd;background:#fff;border-radius:10px;padding:8px 12px;cursor:pointer}
</style>
</head>
<body>
<h2>ข้อสอบนักธรรมชั้นตรี (ออฟไลน์)</h2>
<p>โหมดออฟไลน์ — ข้อมูลจะไม่ถูกส่งออกนอกรายอุปกรณ์</p>
<div id="list"></div>
<div class="score"><button class="btn" id="sumBtn">สรุปคะแนน</button></div>
<script>
async function load(){
  const res = await fetch('data/questions_sample.json?v='+Date.now());
  const data = await res.json();
  const box = document.getElementById('list');
  data.questions.forEach((q,i)=>{
    const div = document.createElement('div');
    div.className='q';
    div.innerHTML = '<b>ข้อ '+(i+1)+'.</b> '+q.text+'<div id="o'+i+'"></div>';
    box.appendChild(div);
    const o = div.querySelector('#o'+i);
    q.options.forEach((t,idx)=>{
      const id = 'q'+i+'_'+idx;
      const label = document.createElement('label');
      label.className='opt';
      label.innerHTML = '<input type="radio" name="q'+i+'" value="'+idx+'" id="'+id+'"> '+t;
      o.appendChild(label);
      o.appendChild(document.createElement('br'));
    });
  });
  document.getElementById('sumBtn').onclick = ()=>{
    let c=0;
    data.questions.forEach((q,i)=>{
      const sel = document.querySelector('input[name="q'+i+'"]:checked');
      if(sel && Number(sel.value)===q.answer) c++;
    });
    alert('คะแนนรวม: '+c+' / '+data.questions.length);
  };
}
load();
</script>
</body></html>
"@ | Out-File -Encoding UTF8 (Join-Path $offlineRoot "index.html")

# data/questions_sample.json
$dataDir = Join-Path $offlineRoot "data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
@"
{
  "title": "Dhamma-Tri Sample Set",
  "questions": [
    { "text": "พุทธคุณ หมายถึงข้อใด", "options": ["คุณของพระพุทธเจ้า","คุณของพระวินัย","คุณของพระธรรม","คุณของพระสงฆ์"], "answer": 0 },
    { "text": "ไตรสิกขา มีอะไรบ้าง", "options": ["ศีล สมาธิ ปัญญา","ทาน ศีล ภาวนา","ขันธ์ ธาตุ อายตนะ","มรรค ผล นิพพาน"], "answer": 0 },
    { "text": "อริยสัจ 4 ข้อใดคือข้อที่ต้องละ", "options": ["ทุกข์","สมุทัย","นิโรธ","มรรค"], "answer": 1 }
  ]
}
"@ | Out-File -Encoding UTF8 (Join-Path $dataDir "questions_sample.json")

# --- สร้าง ZIP แพ็กออฟไลน์ ---
$zipRoot = Join-Path $work "v2/console/assets/offline/dhamma-tri_pack.zip"
if(Test-Path $zipRoot){ Remove-Item -Force $zipRoot }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory((Split-Path $offlineRoot -Parent), $zipRoot)

# --- ปรับ Overview/Projects ให้ไม่ขาว (mock text ถ้ายังไม่ได้ทำ) ---
$ov = Join-Path $work "v2/console/tabs/overview.html"
$pr = Join-Path $work "v2/console/tabs/projects.html"
if((Get-Content $ov -Raw) -match "\[\s*v2/console/tabs/overview.html\s*\]"){
  @"
<h2>Overview (Mock)</h2>
<ul>
  <li>11 Projects: snapshot (mock) คืนนี้ใช้งานได้</li>
  <li>สถานะระบบ: ✓ หน้าแรกเสถียร / ✓ ทางเข้านักธรรมชั้นตรี</li>
  <li>หมายเหตุ: พรุ่งนี้เชื่อม live API และ health JSON</li>
</ul>
"@ | Out-File -Encoding UTF8 $ov
}
if((Get-Content $pr -Raw) -match "\[\s*v2/console/tabs/projects.html\s*\]"){
  @"
<h2>Projects (11 โครงการ) – Mock</h2>
<table border="1" cellpadding="6" cellspacing="0">
  <tr><th>โครงการ</th><th>%คืบหน้า</th><th>มูลค่า</th><th>เทียบเมื่อวาน</th></tr>
  <tr><td>SatiShift</td><td>62%</td><td>—</td><td style="color:green">+2%</td></tr>
  <tr><td>ArunRoo</td><td>54%</td><td>—</td><td style="color:red">-1%</td></tr>
  <tr><td>KMA</td><td>48%</td><td>—</td><td>=</td></tr>
</table>
<p style="color:#666">* ข้อมูล mock คืนนี้ – พรุ่งนี้แทนที่ด้วย live</p>
"@ | Out-File -Encoding UTF8 $pr
}

# --- Commit & Push ---
Push-Location $work
git add -A
git commit -m ("feat(dhamma-tri): landing+offline pack ready (rev {0})" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) | Out-Null

if($env:GITHUB_TOKEN){
  $remote = "https://$($env:GITHUB_TOKEN)@github.com/$Owner/$Repo.git"
  git push $remote $Branch | Out-Null
  Ok "Pushed to $Owner/$Repo:$Branch"
  Pop-Location
  Ok ("Done. Open: {0}/?v={1}#/dhamma-tri" -f $DashBase, (Get-Date -Format "yyyyMMddHHmmss"))
} else {
  Warn "ไม่มี GITHUB_TOKEN → ยังไม่ push (ไฟล์พร้อมแล้วใน $work)"
  Pop-Location
  Write-Host "ตรวจไฟล์แล้ว push เอง: `n  cd `"$work`" ; git push origin $Branch" -ForegroundColor DarkGray
  Write-Host ("Preview local (หลัง push): {0}/?v={1}#/dhamma-tri" -f $DashBase, (Get-Date -Format "yyyyMMddHHmmss")) -ForegroundColor DarkGray
}
