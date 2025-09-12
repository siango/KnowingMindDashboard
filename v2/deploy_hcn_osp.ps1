<# deploy_hcn_osp.ps1 — One-Shot deploy FTPS ด้วย WinSCP #>
param(
  [string]$FtpHost   = "ftp.z62468-oo2336.ps06.zwhhosting.com",
  [int]   $FtpPort   = 21,
  [string]$FtpUser   = "moveweb@huaichannueatemple.com",
  [string]$FtpPass   = $env:FTP_PASS,          # แนะนำตั้งใน ENV: setx FTP_PASS "your_password"
  [string]$LocalDir  = "$PSScriptRoot\site_dist",
  [string]$RemoteDir = "/public_html"
)

$ErrorActionPreference = 'Stop'
function Info($m){ Write-Host "[i] $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "[✓] $m" -ForegroundColor Green }
function Die($m){ Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $LocalDir)) { Die "ไม่พบโฟลเดอร์ต้นทาง: $LocalDir" }
if ([string]::IsNullOrEmpty($FtpPass)) { Die "กรุณาตั้งค่า FTP_PASS เป็นรหัสผ่าน (setx FTP_PASS \"...\" แล้วเปิด PowerShell ใหม่)" }

# ดาวน์โหลด WinSCP แบบพกพา หากยังไม่มี
$Bin = "$PSScriptRoot\winscp"
$Exe = Join-Path $Bin "WinSCP.com"
if (-not (Test-Path $Exe)) {
  Info "ดาวน์โหลด WinSCP portable..."
  New-Item -Type Directory -Force $Bin | Out-Null
  $zip = Join-Path $Bin "winscp.zip"
  Invoke-WebRequest -Uri "https://winscp.net/download/WinSCP-Portable.zip" -OutFile $zip
  Expand-Archive -Path $zip -DestinationPath $Bin -Force
  Remove-Item $zip -Force
  # ค้นหา WinSCP.com ที่ถูกต้อง
  $Exe = Get-ChildItem $Bin -Recurse -Filter WinSCP.com | Select-Object -First 1 -Expand FullName
  if (-not $Exe) { Die "ไม่พบ WinSCP.com หลังแตกไฟล์" }
}

# เขียนสคริปต์คำสั่งให้ WinSCP
$Tmp = New-Item -Type File -Path (Join-Path $env:TEMP "winscp_deploy_hcn.txt") -Force
$sessionUrl = ("ftpes://{0}:{1}@{2}:{3} -explicit" -f ($FtpUser, $FtpPass, $FtpHost, $FtpPort)) `
              -replace '\^', '%%5E'   # กันอักขระพิเศษหายาก

@"
option batch abort
option confirm off
open $sessionUrl
# สร้างโฟลเดอร์ถ้ายังไม่มี
call mkdir $RemoteDir
# อัปโหลดแบบ mirror: ลบไฟล์ส่วนเกิน ปรับโหมด binary เปิด parallel
option transfer binary
option exclude "*/.git*/"
mirror -delete -transfer=binary -resume -parallel=4 "$LocalDir" "$RemoteDir"
ls -l "$RemoteDir"
exit
"@ | Set-Content -Path $Tmp -Encoding ASCII

Info "เริ่มอัปโหลดด้วย WinSCP..."
& $Exe "/script=$Tmp" "/log=$PSScriptRoot\winscp_deploy.log"
Ok "อัปโหลดเสร็จ ตรวจดู log: $PSScriptRoot\winscp_deploy.log"
