# ===== OSP9: Update GitHub Pages Dashboard (cache-bust or publish folder) =====
param(
  [string]$Owner          = "siango",
  [string]$Repo           = "KnowingMindDashboard",
  [string]$Branch         = "gh-pages",
  # ถ้าเว้นว่าง => โหมด Cache-Bust; ถ้าระบุ => โหมดเผยแพร่จากโฟลเดอร์นี้
  [string]$ContentDir     = "",
  # ปลายทางบน gh-pages: "" = root หรือเช่น "v2"
  [string]$DestSubfolder  = "",
  # ถ้า true จะลบปลายทางก่อนคัดลอก (เฉพาะกรณีเผยแพร่จากโฟลเดอร์)
  [bool]  $CleanDest      = $true
)

$ErrorActionPreference = 'Stop'
function Info($m){ Write-Host "[i] $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "[✓] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Die($m){ Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

if (-not $env:GITHUB_TOKEN) { Die "GITHUB_TOKEN missing. setx GITHUB_TOKEN \"ghp_xxx\" แล้วเปิด PowerShell ใหม่" }

$ts = (Get-Date).ToString("yyyyMMdd-HHmmss")

# เตรียม workspace
$work = Join-Path $env:TEMP ("kms_pages_" + $ts)
New-Item -ItemType Directory -Force -Path $work | Out-Null
Set-Location $work

# โคลนแบบเบา
git init | Out-Null
$remote = "https://$($env:GITHUB_TOKEN)@github.com/$Owner/$Repo.git"
git remote add origin $remote
git fetch --depth 1 origin $Branch
git checkout -b $Branch ("origin/" + $Branch)

# โหมดเผยแพร่จากโฟลเดอร์
if ($ContentDir) {
  if (-not (Test-Path $ContentDir)) { Die "ไม่พบโฟลเดอร์ ContentDir: $ContentDir" }

  $dest = $PWD
  if ($DestSubfolder) {
    $dest = Join-Path $PWD $DestSubfolder
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
  }

  if ($CleanDest -and (Test-Path $dest)) {
    Info "ล้างปลายทาง: $dest"
    Get-ChildItem -Path $dest -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  }

  Info "คัดลอกไฟล์จาก $ContentDir → $dest"
  Copy-Item -Path (Join-Path (Resolve-Path $ContentDir) "*") -Destination $dest -Recurse -Force

  # cache-bust
  Set-Content -Path (Join-Path $PWD ".cache-bust-$ts") -Value $ts -Encoding UTF8

  git add .
  git -c user.email="bot@kms" -c user.name="KMSBot" commit -m "publish($DestSubfolder): $ts" | Out-Null
  git push origin $Branch

  if ($DestSubfolder) {
    $pagesUrl = "https://$Owner.github.io/$Repo/$DestSubfolder/"
  } else {
    $pagesUrl = "https://$Owner.github.io/$Repo/"
  }
  Ok "เผยแพร่ไฟล์แล้ว"
  Info "เปิดดู: $pagesUrl"
  exit 0
}

# โหมด Cache-Bust
$marker = ".cache-bust-$ts"
Set-Content -Path (Join-Path $PWD $marker) -Value $ts -Encoding UTF8
git add $marker
git -c user.email="bot@kms" -c user.name="KMSBot" commit -m "cache-bust $ts" | Out-Null
git push origin $Branch

$pagesUrl = "https://$Owner.github.io/$Repo/"
Ok "Cache-Bust สำเร็จ"
Info "เปิดดู: $pagesUrl"
