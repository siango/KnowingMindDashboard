<# ======================================================================
  OSP9: KMS Dashboard Ship (All-in-One, SAFE)
  - สร้าง snapshot (JSON/MD/CSV)
  - แตกข้อมูล 11 โปรเจกต์เป็น /v2/console/data/*.json + projects_index.json
  - (ออปชัน) Preflight + POST snapshot ไปปลายทาง
  - เขียน/แพตช์ UI: _auto_tabs_block.html, CSS badges+glass, SPA 404
  - ขยาย app.js (โหลดแท็บแบบ async) ด้วย single-quoted here-string (กัน $ โดน PS ขยาย)
  - ใส่ overview.boot.js ให้หน้า default = Overview (กดไปยัง 11 โปรเจกต์ได้)
  - เขียน version.json + cache_bust_xxx.txt
  - commit + push gh-pages
====================================================================== #>

param(
  [string]$Owner          = 'siango',
  [string]$Repo           = 'KnowingMindDashboard',
  [string]$Branch         = 'gh-pages',
  [string]$SubDir         = 'v2/console',   # web root under gh-pages
  [string]$DataDir        = 'data',         # JSON tabs live here
  [string]$PagesLocalPath = '',             # local gh-pages repo (empty = clone)
  [string]$DestUrl        = $env:DEST_URL,  # POST endpoint (optional)
  [string]$ApiKey         = $env:API_KEY,   # X-API-Key (optional)
  [int]   $TimeoutSec     = 12
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

function Info($m){ Write-Host "[i] $m" -ForegroundColor Cyan }
function Ok($m){   Write-Host "[✓] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Die($m){  Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

# --- clock / paths ---
$tsIso  = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
$stamp  = Get-Date -Format "yyyyMMdd-HHmmss"
$verTag = "v-$stamp"

# --- clone or use local gh-pages ---
if (-not $PagesLocalPath) {
  $tmp = Join-Path $env:TEMP "kms_pages_$stamp"
  $repoUrl = if($env:GITHUB_TOKEN){ "https://$($env:GITHUB_TOKEN)@github.com/$Owner/$Repo.git" } else { "https://github.com/$Owner/$Repo.git" }
  Info "Cloning $Owner/$Repo ($Branch) -> $tmp"
  git clone --branch $Branch --single-branch $repoUrl $tmp | Out-Null
  $DashRoot = $tmp
} else {
  $DashRoot = (Resolve-Path $PagesLocalPath).Path
  Info "Using local repo: $DashRoot"
  if (-not (Test-Path (Join-Path $DashRoot ".git"))) { Die "Not a git repo: $DashRoot" }
  git -C $DashRoot checkout $Branch | Out-Null
  git -C $DashRoot pull --ff-only | Out-Null
}
Ok "Repo ready."

# --- important dirs ---
$DashSubdir  = $SubDir -replace '^[\\/]+',''
$DashDir     = Join-Path $DashRoot $DashSubdir
$DashDataDir = Join-Path $DashDir  $DataDir
$SnapDir     = Join-Path $DashRoot "snapshots"
$JsDir       = Join-Path $DashDir  "assets/js"
$CssDir      = Join-Path $DashDir  "assets/css"

New-Item -ItemType Directory -Force -Path $DashDir,$DashDataDir,$SnapDir,$JsDir,$CssDir | Out-Null

# --------------------------- PROJECT PAYLOAD (11) ---------------------------
$projectsJson = @'
{
  "projects": [
    {"id":"kma","name":"KnowingMindApp (KMA)","status":"yellow",
     "latest":"Cloud Run kma-api online (ingress internal+LB, min=1) มี /api/ping; Firestore native multi-tenant; CI/CD WIF + Artifact Registry",
     "architecture":"Cloud Run(kma-api) ↔ Firestore ↔ Secret Manager ↔ (แผน) External HTTPS LB/Workflows",
     "risks":"ยังเรียกตรงจากอินเทอร์เน็ตไม่ได้เพราะ ingress จำกัด",
     "next_steps":{"h48":"ตัดสินใจวิธีเข้าภายนอก: LB (Serverless NEG) หรือ Workflows/Scheduler",
                   "d7":"Docs API (หนังสือเข้า/ออก) + Smoke tests CI",
                   "d30":"หน้า Admin เอกสาร + usage report"}
    },
    {"id":"arunroo","name":"อรุณรู้ (ArunRoo)","status":"green",
     "latest":"n8n Cloud โพสต์รายวัน FB/IG/YT/TikTok (07:30–07:50); Podcast Readcast REV-E (sheet podcast_queue)",
     "architecture":"n8n ↔ Google Sheets ↔ Social ↔ (ตัวเลือก) KMA hook",
     "risks":"โควต้าโพสต์/สิทธิ์สื่อ",
     "next_steps":{"h48":"notifier เมื่อโพสต์ล้มเหลว + log ต่อแพลตฟอร์ม",
                   "d7":"UTM→funnel เข้า KMA + analytics",
                   "d30":"monthly recap อัตโนมัติ (ภาพ+พอดแคสต์)"}
    },
    {"id":"satishift","name":"สติเวร (SatiShift)","status":"green",
     "latest":"satishift-webhook(public), satishift-fullstack(00002)/health; Scheduler */5 ยัง UTC; auto rotation, NimmanQueue, AlmsRoutes",
     "architecture":"LINE OA ↔ Apps Script/Sheets ↔ Full-Stack(Node/TS) ↔ Firestore ↔ Scheduler/Secrets",
     "risks":"Timezone เพี้ยน, ต้องพิสูจน์ fairness",
     "next_steps":{"h48":"ตั้ง Scheduler Asia/Bangkok + log health",
                   "d7":"สคริปต์ตรวจความยุติธรรม + รายงานสัปดาห์",
                   "d30":"Temple Starter Kit รีลีส + README ล่าสุด"}
    },
    {"id":"dashboard","name":"KnowingMindDashboard (/v2)","status":"yellow",
     "latest":"UI แท็บพร้อม + mock สี; เคยเจอ cache/branch mapping",
     "architecture":"gh-pages ↔ (แผน) GAS/KMA data source",
     "risks":"แคชไม่เห็นการเปลี่ยนแปลงทันที",
     "next_steps":{"h48":"ฝัง health ของ 3 Cloud Run + n8n last run",
                   "d7":"ปุ่ม Update now + SW/cache bust auto",
                   "d30":"กราฟ KPI + แผงผู้บริหาร"}
    },
    {"id":"ai_creator","name":"AI Creator","status":"yellow",
     "latest":"เตรียม route /ai/creator/* + Firestore collections",
     "architecture":"kma-api ↔ Firestore ↔ n8n ↔ Secrets",
     "risks":"ต้นทุน/โควต้า AI, คุณภาพคอนเทนต์",
     "next_steps":{"h48":"POST /ai/creator/tasks + job runner",
                   "d7":"feedback loop + metric time-to-publish",
                   "d30":"self-init + แดชบอร์ดคุณภาพ"}
    },
    {"id":"beyond_the_rich","name":"Beyond the Rich (MoneyAI)","status":"orange",
     "latest":"sandbox ภายใน; ความสด/ความตรงของผลสดยังไม่นิ่ง",
     "architecture":"Multi sources ↔ วิเคราะห์ ↔ dashboard/chat",
     "risks":"ความแม่นข้อมูล, ความเสี่ยงทางการเงิน",
     "next_steps":{"h48":"รวมหลายแหล่ง + consensus + time-stamp",
                   "d7":"hedge/cover module + PnL sim",
                   "d30":"กรอบจริยธรรม + training-only mode"}
    },
    {"id":"boonroo","name":"BoonRoo (Donation)","status":"yellow",
     "latest":"Phase1: PromptPay QR + webhook→Firestore + PDF ใบอนุโมทนา; Budget Alert ผ่าน LINE",
     "architecture":"kma-api ↔ Firestore ↔ Payments ↔ Secrets ↔ LINE/Email",
     "risks":"ความถูกต้องใบอนุโมทนา/ภาษี, ความปลอดภัยคีย์",
     "next_steps":{"h48":"/donations/hook + PDF stub",
                   "d7":"Dashboard ยอดรายวัน + LINE Alert",
                   "d30":"Stripe/Opn + Apple/Google Pay"}
    },
    {"id":"roblox","name":"Roblox Mindful Life","status":"orange",
     "latest":"แนวคิด/สคริปต์ตั้งต้น; ยังไม่ production",
     "architecture":"Roblox(Lua) ↔ GitHub ↔ (แผน) kma-api",
     "risks":"ทรัพยากร/เวลา, นโยบายแพลตฟอร์ม",
     "next_steps":{"h48":"repo + แผนที่ต้นแบบ + โหมดฝึก 1 นาที",
                   "d7":"hook ส่ง Mindful Minutes กลับ KMA",
                   "d30":"ทดสอบเล็ก + telemetry บนแดชบอร์ด"}
    },
    {"id":"gov_docs","name":"Government-Docs (KMA)","status":"yellow",
     "latest":"สคีมา Firestore docs/sequences/events/notifications พร้อม",
     "architecture":"kma-api ↔ Firestore ↔ LINE/Email ↔ (แผน) e-Signature",
     "risks":"เลขรับ-ส่ง/เวิร์กโฟลว์เซ็น, PDPA",
     "next_steps":{"h48":"POST /docs/incoming + running number",
                   "d7":"Inbox view + แจ้งเตือน",
                   "d30":"ส่งต่อ/มอบหมาย/เซ็น + audit trail"}
    },
    {"id":"eboard","name":"e-Board / Event Broadcasting","status":"yellow",
     "latest":"วางแผนหน้า e-Board + เชื่อม FB/YT",
     "architecture":"GH Pages/KMA Frontend ↔ KMA API ↔ FB/YT/LINE",
     "risks":"สิทธิ์/โควต้า API, freshness",
     "next_steps":{"h48":"หน้า e-Board static (mock→live slots)",
                   "d7":"ตัวดึงโพสต์/อัลบั้ม/คลิปสอน + แคช",
                   "d30":"โหมดป้ายประกาศ (auto refresh) + ปฏิทิน"}
    },
    {"id":"lms","name":"LMS / Meditation Courses","status":"yellow",
     "latest":"Roadmap คอร์สพื้นฐาน + ใบประกาศ + multilingual",
     "architecture":"KMA Frontend ↔ kma-api ↔ Payments ↔ Certificates(PDF) ↔ Analytics",
     "risks":"ประสบการณ์เรียน/Completion",
     "next_steps":{"h48":"Course Landing + Checkout (mock)",
                   "d7":"POST /courses/enroll + PDF template",
                   "d30":"MVP 1 คอร์ส + metrics"}
    }
  ]
}
'@

$payload = $projectsJson | ConvertFrom-Json

# --------------------------- WRITE SNAPSHOT (JSON/MD/CSV) ---------------------------
$snapJson = Join-Path $SnapDir "kms_chat_snapshot_${stamp}.json"
$snapMd   = Join-Path $SnapDir "kms_chat_snapshot_${stamp}.md"
$snapCsv  = Join-Path $SnapDir "kms_chat_snapshot_${stamp}.csv"

$snapshot = [ordered]@{ timestamp=$tsIso; timezone="Asia/Bangkok"; source="chat"; suite="KnowingMindSuite"; data=$payload }
$snapshot | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $snapJson
"# KMS Snapshot $stamp`nGenerated: $tsIso (Asia/Bangkok)`nIncludes 11 projects." | Set-Content -Encoding UTF8 $snapMd

$rows = foreach($p in $payload.projects){
  [pscustomobject]@{
    id=$p.id; name=$p.name; status=$p.status; latest=$p.latest; architecture=$p.architecture; risks=$p.risks
    h48=$p.next_steps.h48; d7=$p.next_steps.d7; d30=$p.next_steps.d30
  }
}
$rows | Export-Csv -Path $snapCsv -NoTypeInformation -Encoding UTF8
Ok "Snapshot written: JSON/MD/CSV"

# --------------------------- SPLIT PER-PROJECT ---------------------------
$indexJson = Join-Path $DashDataDir "projects_index.json"
$index     = [ordered]@{ generated_at=$tsIso; timezone="Asia/Bangkok"; items=@() }

foreach($p in $payload.projects){
  $f = Join-Path $DashDataDir ($p.id + ".json")
  $one = [ordered]@{
    id=$p.id; name=$p.name; status=$p.status; updated_at=$tsIso
    latest=$p.latest; architecture=$p.architecture; risks=$p.risks
    next_steps = [ordered]@{ h48=$p.next_steps.h48; d7=$p.next_steps.d7; d30=$p.next_steps.d30 }
  }
  $one | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $f
  $index.items += @{ id=$p.id; name=$p.name; status=$p.status }
}
$index | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $indexJson
Ok "Split 11 projects -> $DataDir/*.json + projects_index.json"

# --------------------------- PRE-FLIGHT + POST (optional) ---------------------------
$posted = $false
if ([string]::IsNullOrWhiteSpace($DestUrl)) {
  Warn "DEST_URL not set → skip POST."
} else {
  try {
    Info "Preflight GET: $DestUrl"
    $gc = Invoke-WebRequest -Uri $DestUrl -TimeoutSec $TimeoutSec -Method GET -ErrorAction Stop
    Info ("GET -> {0}" -f $gc.StatusCode)
  } catch { Warn ("GET preflight failed: {0}" -f $_.Exception.Message) }

  try {
    Info "Preflight POST (ping)"
    $hdr = @{ "Content-Type"="application/json" }
    if ($ApiKey) { $hdr["X-API-Key"] = $ApiKey }
    $pc = Invoke-WebRequest -Uri $DestUrl -TimeoutSec $TimeoutSec -Method POST -Headers $hdr -Body '{"ping":"kms"}' -ErrorAction Stop
    Info ("POST ping -> {0}" -f $pc.StatusCode)
    if ($pc.StatusCode -in 200,201) { $posted = $true }
  } catch { Warn ("POST preflight failed: {0}" -f $_.Exception.Message) }
  if ($posted) {
    try {
      Info "POST snapshot JSON..."
      $hdr = @{ "Content-Type"="application/json" }
      if ($ApiKey) { $hdr["X-API-Key"] = $ApiKey }
      $rc = Invoke-WebRequest -Uri $DestUrl -TimeoutSec $TimeoutSec -Method POST -Headers $hdr -InFile $snapJson -ErrorAction Stop
      Ok  ("POST snapshot -> {0}" -f $rc.StatusCode)
    } catch { Warn ("POST snapshot failed: {0}" -f $_.Exception.Message) }
  } else {
    Warn "Endpoint didn't accept POST ping; kept local files only."
  }
}

# --------------------------- UI PATCHES ---------------------------

# 1) _auto_tabs_block.html from index items
$navBlockPath = Join-Path $DashDir '_auto_tabs_block.html'
$json  = Get-Content -LiteralPath $indexJson -Raw | ConvertFrom-Json
$items = $json.items
$links = (
  $items | ForEach-Object {
    '<a href="#/{0}" class="nav-link"><span class="txt">{1}</span></a>' -f $_.id, $_.name
  }
) -join [Environment]::NewLine
@"
<!-- KM-AUTO-TABS:BEGIN -->
<!-- generated at $tsIso -->
$links
<!-- KM-AUTO-TABS:END -->
"@ | Set-Content -LiteralPath $navBlockPath -Encoding UTF8
Ok "_auto_tabs_block.html written."

# 2) CSS (badges + glass) – append once (safe)
$cssPath = Join-Path $CssDir 'style.css'
if (-not (Test-Path $cssPath)) { New-Item -ItemType File -Force -Path $cssPath | Out-Null }
if (-not (Select-String -Path $cssPath -Pattern 'KM-AUTO:BADGES-GLASS' -SimpleMatch -Quiet)) {
@'
/* KM-AUTO:BADGES-GLASS */
.badge{display:inline-block;padding:.2rem .6rem;border-radius:.5rem;font-size:.8rem;font-weight:600}
.badge.live {background:#16a34a22;color:#22c55e;border:1px solid #22c55e55}
.badge.mock {background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b55}
.badge.stale{background:#6b728022;color:#9ca3af;border:1px solid #9ca3af55}
.glass{background:rgba(20,24,34,.35);backdrop-filter:saturate(140%) blur(8px);-webkit-backdrop-filter:saturate(140%) blur(8px);border:1px solid rgba(255,255,255,.08)}
.card.glass{box-shadow:0 10px 30px rgba(0,0,0,.25)}
.nav-link{color:#b7c0d1;text-decoration:none;margin-right:.75rem}
.nav-link.active{color:#60a5fa}
.muted{opacity:.7}
.progress{height:6px;background:#1f2937;border-radius:6px;overflow:hidden}
.progress>span{display:block;height:100%;width:0;transition:width .8s ease}
'@ | Add-Content -Path $cssPath -Encoding UTF8
  Ok "CSS badges + glass appended."
} else { Info "CSS already patched." }

# 3) 404.html SPA fallback
$spa404 = @"
<!doctype html><meta charset=""utf-8""><title>Redirecting…</title>
<script>(function(){var p=location.pathname+location.search+location.hash;
var t='/'+('$DashSubdir')+'/index.html'+(location.hash||'');
if(!location.hash&&p&&p!=='/404.html'){t+='#'+encodeURIComponent(p);}location.replace(t);}())</script>
"@
Set-Content -Encoding UTF8 -Path (Join-Path $DashDir "404.html") -Value $spa404
Ok "404 SPA fallback ready."

# 4) Extend assets/js/app.js (SAFE here-string; no $ expansion)
$jsPath = Join-Path $JsDir 'app.js'
if (-not (Test-Path $jsPath)) { New-Item -ItemType File -Force -Path $jsPath | Out-Null }
$appPatch = @'
;/* KM-AUTO:APP-EXT v2 (safe) */
(async function(){
  const $  = (q,d=document)=>d.querySelector(q);
  const $$ = (q,d=document)=>Array.from(d.querySelectorAll(q));
  const VER = (window.KM_VER||'') + '';
  const DATA_BASE = './@@D@@/';

  async function getJSON(f){
    const u = DATA_BASE + f + (VER?('?v='+VER):'');
    const r = await fetch(u,{cache:'no-store'});
    if(!r.ok) throw new Error('HTTP '+r.status+' '+u);
    return await r.json();
  }

  async function loadProject(id){
    const content = $('#content'); content.innerHTML = '<div class="loading">Loading…</div>';
    try{
      const p = await getJSON(id+'.json');
      const badge = (s)=> s==='green' ? 'live' : (s==='yellow'?'mock':'stale');
      const pct   = (s)=> s==='green'?88:(s==='yellow'?55:25);
      content.innerHTML = `
        <div class="card glass" style="padding:1rem 1.25rem">
          <h2 style="margin:.2rem 0 1rem 0">${p.name} <span class="badge ${badge(p.status)}">${p.status}</span></h2>
          <div style="margin:.5rem 0 1rem 0" class="muted">${p.latest}</div>
          <div class="progress" data-value="${pct(p.status)}"><span></span></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px">
            <div class="card glass" style="padding:12px"><h3>Architecture</h3><p class="muted">${p.architecture}</p></div>
            <div class="card glass" style="padding:12px"><h3>Risks</h3><p class="muted">${p.risks||'-'}</p></div>
          </div>
          <div class="card glass" style="padding:12px;margin-top:16px">
            <h3>Next steps</h3>
            <ul>
              <li><b>48ชม.</b> ${p.next_steps.h48||'-'}</li>
              <li><b>7วัน</b> ${p.next_steps.d7||'-'}</li>
              <li><b>30วัน</b> ${p.next_steps.d30||'-'}</li>
            </ul>
            <p class="muted">updated at ${p.updated_at||''}</p>
          </div>
        </div>`;
      requestAnimationFrame(()=>{$('.progress>span',content).style.width =
        Math.max(0,Math.min(100, Number($('.progress',content).dataset.value||0)))+'%';});
      $$('.nav-link').forEach(a=>a.classList.toggle('active', a.getAttribute('href')==='#/'+id));
    }catch(e){
      content.innerHTML = '<div class="card"><h3>Load error</h3><pre>'+String(e.message||e)+'</pre></div>';
      console.error(e);
    }
  }

  // hydrate nav from _auto_tabs_block.html
  try{
    const holder = document.querySelector('.sidebar nav'); if(holder){
      const resp = await fetch('./_auto_tabs_block.html'+(VER?('?v='+VER):''),{cache:'no-store'});
      if(resp.ok){ holder.innerHTML = await resp.text(); }
    }
  }catch(_){}

  function route(){
    const id = (location.hash.replace(/^#\/?/,'') || 'overview');
    if(id==='overview'){ if(typeof window.KM_OV==='function'){ window.KM_OV(); } else { setTimeout(route,60); } return; }
    loadProject(id);
  }
  window.addEventListener('hashchange', route);
  document.addEventListener('DOMContentLoaded', route);
}());
'@
$appPatch = $appPatch.Replace('@@D@@',$DataDir)
if (-not (Select-String -Path $jsPath -Pattern 'KM-AUTO:APP-EXT v2 (safe)' -SimpleMatch -Quiet)) {
  Add-Content -Path $jsPath -Value $appPatch -Encoding UTF8
  Ok "app.js extended (async tabs + badges, SAFE)."
} else { Info "app.js already extended (safe)." }

# 5) overview.boot.js (default page loader)
$bootJs = Join-Path $JsDir 'overview.boot.js'
$ov = @'
/* KM-OV-BOOT (safe) */
(() => {
  const $  = (q,d=document)=>d.querySelector(q);
  const $$ = (q,d=document)=>Array.from(d.querySelectorAll(q));
  const VER = (window.KM_VER||'') + '';
  const DATA_BASE = './@@D@@/';
  async function getJSON(n){ const u=DATA_BASE+n+(VER?('?v='+VER):''); const r=await fetch(u,{cache:'no-store'}); if(!r.ok) throw new Error('HTTP '+r.status+' '+u); return r.json(); }
  window.KM_OV = async function(){
    const el = $('#content'); if(!el) return;
    el.innerHTML = '<div class="card glass" style="padding:16px">Loading overview...</div>';
    try{
      const idx = await getJSON('projects_index.json');
      const cards = (idx.items||[]).map(it=>`
        <a class="card glass" href="#/${it.id}" style="padding:14px;display:block;text-decoration:none">
          <h3 style="margin:0 0 6px 0">${it.name}</h3>
          <p class="muted" style="margin:0">ดูรายละเอียดโครงการ</p>
        </a>`).join('');
      el.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px">${cards}</div>`;
    }catch(e){
      el.innerHTML = '<div class="card glass" style="padding:16px"><h3>Overview error</h3><pre>'+String(e.message||e)+'</pre></div>';
    }
  };
  if ((location.hash||'').replace(/^#\/?/,'')==='overview') window.KM_OV();
})();
'@
$ov = $ov.Replace('@@D@@',$DataDir)
Set-Content -Encoding UTF8 $bootJs -Value $ov
Ok "overview.boot.js written."

# 6) Patch index.html -> inject overview.boot.js if absent
$idxPath = Join-Path $DashDir 'index.html'
if (Test-Path $idxPath) {
  $ix = Get-Content -LiteralPath $idxPath -Raw
  if ($ix -notmatch 'overview\.boot\.js') {
    $inject = "<script src=""assets/js/overview.boot.js?v=$verTag""></script>"
    $ix2 = $ix -ireplace '</body>', ($inject + "`n</body>")
    Set-Content -LiteralPath $idxPath -Encoding UTF8 -Value $ix2
    Ok "index.html patched."
  } else {
    Info "index.html already has overview boot."
  }
} else {
  Warn "index.html not found at $idxPath (skip inject)."
}

# 7) Version markers (cache-bust)
$verFile = Join-Path $DashRoot "version.json"
@("{" + '"version":"'+$verTag+'","generated":"'+$tsIso+'"' + "}") | Set-Content -Encoding UTF8 $verFile
Set-Content -Encoding UTF8 -Path (Join-Path $DashRoot ("cache_bust_"+$verTag+".txt")) -Value "bust $verTag"
Ok "Version markers written: $verTag"

# --------------------------- COMMIT + PUSH ---------------------------
Push-Location $DashRoot
try { git add -A | Out-Null } catch {}
try {
  git commit -m "[data/ui] 11 projects + UI patch + overview boot ($tsIso)" | Out-Null
} catch { }
try {
  git push origin $Branch | Out-Null
  Ok "Pushed to gh-pages."
} catch { Warn ("git push failed: {0}" -f $_.Exception.Message) }
Pop-Location

# --------------------------- SUMMARY ---------------------------
Ok "DONE"
Write-Host ("JSON  : {0}" -f $snapJson)
Write-Host ("MD    : {0}" -f $snapMd)
Write-Host ("CSV   : {0}" -f $snapCsv)
Write-Host ("Data  : {0}" -f $DashDataDir)

# Show best-guess URL
$baseUrl = "https://$Owner.github.io/$Repo"
$cnamePath = Join-Path $DashRoot 'CNAME'
if (Test-Path $cnamePath) {
  $cd = (Get-Content $cnamePath -Raw).Trim()
  if ($cd) { $baseUrl = "https://$cd" }
}
Write-Host ("Open: {0}/{1}/#/overview  (default Overview)" -f $baseUrl, ($DashSubdir -replace '\\','/'))
Write-Host ("Detail pages (11 โครงการ): {0}/{1}/#/kma  (เปลี่ยน id เป็นแต่ละโปรเจกต์)" -f $baseUrl, ($DashSubdir -replace '\\','/'))
# ======================================================================
