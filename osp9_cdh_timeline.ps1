# C:\AndroidProjects\KnowingMindSuite\osp9_cdh_timeline.ps1
<#
 OSP9 — CDH Timeline Builder (v2, null-safe)
 - อ่าน snapshots จาก LOCAL แล้วสร้างรายงาน timeline (CSV/MD)
 - ป้องกัน null จากไฟล์เสีย/อ่านไม่ได้
 Output:
   %USERPROFILE%\knowingmind-vault\KMS_CDH\reports\kms_timeline_latest.csv
   %USERPROFILE%\knowingmind-vault\KMS_CDH\reports\kms_timeline_latest.md
#>

param(
  [int]$Max = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Ok($m){ Write-Host "✓ $m" -ForegroundColor Green }
function Warn($m){ Write-Host "! $m" -ForegroundColor Yellow }
function Info($m){ Write-Host "i $m" -ForegroundColor Cyan }
function New-IfMissing($p){ if(-not (Test-Path $p)){ New-Item -ItemType Directory -Force -Path $p | Out-Null } }

function Parse-Item([string]$jsonText,[string]$source,[string]$pathLike){
  if([string]::IsNullOrWhiteSpace($jsonText)){ return $null }
  try{ $obj = $jsonText | ConvertFrom-Json -Depth 64 } catch { return $null }

  $done = 0; $next = 0; $blockers = 0; $notes = ""
  if($obj.tasks){
    if($obj.tasks.done){     $done     = @($obj.tasks.done).Count }
    if($obj.tasks.next){     $next     = @($obj.tasks.next).Count }
    if($obj.tasks.blockers){ $blockers = @($obj.tasks.blockers).Count }
    if($obj.tasks.notes){    $notes    = [string]$obj.tasks.notes }
  }

  [pscustomobject]@{
    source = $source
    path   = $pathLike
    generated_at_local = $obj.generated_at_local
    timezone = $obj.timezone
    host = $obj.host
    user = $obj.user
    done = $done
    next = $next
    blockers = $blockers
    notes = $notes
  }
}

# 1) Load from LOCAL
$vault = Join-Path $env:USERPROFILE "knowingmind-vault\KMS_CDH\snapshots"
if(-not (Test-Path $vault)){
  Warn "ไม่พบโฟลเดอร์ $vault"; exit 0
}
$files = Get-ChildItem -LiteralPath $vault -Filter *.json -File -ErrorAction SilentlyContinue
$rows  = @()
$skipped = 0
foreach($f in $files){
  try{
    $txt  = Get-Content -Raw -Path $f.FullName -Encoding UTF8
    $item = Parse-Item $txt "local" $f.Name
    if($null -ne $item){ $rows += $item } else { $skipped++ }
  } catch { $skipped++ }
}
if($skipped -gt 0){ Warn "ข้ามไฟล์ที่อ่านไม่ได้/JSON เสีย: $skipped ไฟล์" }

# 2) FILTER OUT NULL (ตัวต้นเหตุ error)
$rows = $rows | Where-Object { $_ -ne $null }

# 3) Add sortable datetime (`dt`) อย่างปลอดภัย
foreach($r in $rows){
  if($null -eq $r){ continue }
  $dt = $null
  if($r.generated_at_local){
    try{ $dt = [datetime]$r.generated_at_local } catch { $dt = $null }
  }
  Add-Member -InputObject $r -NotePropertyName dt -NotePropertyValue $dt -Force
}

# 4) Sort (เก่าสุด -> ล่าสุด) แล้ว limit หากมี Max
$rows = $rows | Sort-Object { if($_.dt){ $_.dt } else { Get-Date "1900-01-01" } }, path
if($Max -gt 0){ $rows = $rows | Select-Object -First $Max }

# 5) Write reports
$reports = Join-Path $env:USERPROFILE "knowingmind-vault\KMS_CDH\reports"
New-IfMissing $reports
$stamp     = (Get-Date).ToString("yyyyMMdd-HHmmss")
$csvPath   = Join-Path $reports ("kms_timeline_" + $stamp + ".csv")
$csvLatest = Join-Path $reports "kms_timeline_latest.csv"
$mdPath    = Join-Path $reports ("kms_timeline_" + $stamp + ".md")
$mdLatest  = Join-Path $reports "kms_timeline_latest.md"

$rows | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8
Copy-Item -Force $csvPath $csvLatest

$header = @(
  "| # | เวลา (local) | แหล่ง | Host | User | Done | Next | Blockers | Notes | ไฟล์ |",
  "|---:|---|---|---|---|---:|---:|---:|---|---|"
)
$lines = @()
$idx = 0
foreach($d in $rows){
  $idx++
  $note = if($d.notes -and $d.notes.Length -gt 120){ $d.notes.Substring(0,117)+"..." } else { $d.notes }
  $lines += "| $idx | $($d.generated_at_local) | $($d.source) | $($d.host) | $($d.user) | $($d.done) | $($d.next) | $($d.blockers) | $note | $($d.path) |"
}
@($header+$lines) | Set-Content -Path $mdPath -Encoding UTF8
Copy-Item -Force $mdPath $mdLatest

Ok "Timeline CSV:  $csvPath"
Ok "Timeline MD:   $mdPath"
Ok "Latest CSV →   $csvLatest"
Ok "Latest MD  →   $mdLatest"
