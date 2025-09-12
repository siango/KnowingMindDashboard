# ===== OSP9: Patch dashboard with clock widget + auto-refresh =====
param(
  [string]$Owner          = "siango",
  [string]$Repo           = "KnowingMindDashboard",
  [string]$Branch         = "gh-pages",
  # โฟลเดอร์ย่อยของ gh-pages: "" = root, หรือเช่น "v2"
  [string]$DestSubfolder  = "",
  # ตั้งเวลารีเฟรชหน้า (นาที). 0 = ไม่ตั้งรีเฟรช
  [int]   $RefreshMinutes = 5,
  # ถ้าไม่พบ index.html ให้สร้างหน้าเริ่มต้น
  [switch]$CreateIfMissing
)

$ErrorActionPreference = 'Stop'
function Info($m){ Write-Host "[i] $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "[✓] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Die($m){ Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

if (-not $env:GITHUB_TOKEN) { Die "GITHUB_TOKEN missing. setx GITHUB_TOKEN \"ghp_xxx\" แล้วเปิด PowerShell ใหม่" }

$ts   = (Get-Date).ToString("yyyyMMdd-HHmmss")
$work = Join-Path $env:TEMP ("kms_pages_" + $ts)
New-Item -ItemType Directory -Force -Path $work | Out-Null
Set-Location $work

# -- clone (shallow) --
git init | Out-Null
$remote = "https://$($env:GITHUB_TOKEN)@github.com/$Owner/$Repo.git"
git remote add origin $remote
git fetch --depth 1 origin $Branch
git checkout -b $Branch ("origin/" + $Branch)

$root = Get-Location
$dest = $root
if ($DestSubfolder) {
  $dest = Join-Path $root $DestSubfolder
  New-Item -ItemType Directory -Force -Path $dest | Out-Null
}
$indexPath = Join-Path $dest "index.html"

# -- ensure index.html --
if (-not (Test-Path $indexPath)) {
  if ($CreateIfMissing) {
    Info "ไม่พบ index.html → สร้างหน้าเริ่มต้น"
    $base = @"
<!DOCTYPE html>
<html lang="th">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>KnowingMind Dashboard</title>
  <style>
    body{font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#f8f9fb;margin:0;padding:24px;}
    .wrap{max-width:1080px;margin:auto}
    h1{margin:0 0 8px}
    .muted{color:#667085}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>KnowingMind Dashboard</h1>
    <p class="muted">หน้าเริ่มต้นถูกสร้างอัตโนมัติ – เพิ่มเนื้อหาได้ตามต้องการ</p>
  </div>
</body>
</html>
"@
    $base | Set-Content -Encoding UTF8 $indexPath
  }
  else {
    Die "ไม่พบ $indexPath (เพิ่ม -CreateIfMissing เพื่อให้สคริปต์สร้างหน้าเริ่มต้นให้)"
  }
}

# -- prepare snippet (idempotent) --
$minutes = [Math]::Max(0, $RefreshMinutes)
$clockSnippet = @"
<!-- KMS_CLOCK_WIDGET -->
<div id="clock" style="position:fixed;bottom:10px;left:10px;font-family:Consolas,monospace;font-size:14px;color:#666;padding:6px 8px;border-radius:8px;background:rgba(255,255,255,.8);box-shadow:0 2px 10px rgba(0,0,0,.06);z-index:9999">--:--</div>
<script>
(function(){
  function updateClock(){
    var now = new Date();
    var el  = document.getElementById("clock");
    if(el){ el.textContent = now.toLocaleString("th-TH", { hour12:false }); }
  }
  setInterval(updateClock, 1000);
  updateClock();
  var refreshMin = $minutes;
  if (refreshMin > 0) {
    setInterval(function(){ location.reload(); }, refreshMin*60*1000);
  }
})();
</script>
<!-- /KMS_CLOCK_WIDGET -->
"@

# -- inject before </body> (avoid duplicate) --
$html = Get-Content -Raw -Encoding UTF8 $indexPath
if ($html -match "KMS_CLOCK_WIDGET") {
  Warn "มี clock widget อยู่แล้ว → ข้ามการแทรก"
} else {
  if ($html -match "</body>") {
    $html = $html -replace "</body>", ($clockSnippet + "`r`n</body>")
  } else {
    $html += "`r`n" + $clockSnippet
  }
  $html | Set-Content -Encoding UTF8 $indexPath
  Ok "แทรก clock widget + auto-refresh (ทุก $minutes นาที)"
}

# -- cache-bust & push --
Set-Content -Path (Join-Path $root ".cache-bust-$ts") -Value $ts -Encoding UTF8
git add .
git -c user.email="bot@kms" -c user.name="KMSBot" commit -m "patch(clock+$($minutes)m): $ts" | Out-Null
git push origin $Branch

# -- output URL --
if ($DestSubfolder) {
  $pagesUrl = "https://$Owner.github.io/$Repo/$DestSubfolder/"
} else {
  $pagesUrl = "https://$Owner.github.io/$Repo/"
}
Ok "Patched & published"
Info "เปิดดู: $pagesUrl"
