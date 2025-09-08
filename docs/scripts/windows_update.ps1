Param(
  [string]$Repo = "$env:USERPROFILE\KnowingMindDashboard",
  [string]$Remote = "https://github.com/siango/KnowingMindDashboard.git",
  [string]$AliasName = $env:COMPUTERNAME,
  [string]$Status = "ok"
)
$ErrorActionPreference = "Stop"

function Need-Jq {
  if (-not (Get-Command jq -ErrorAction SilentlyContinue)) {
    Write-Host "Please install jq first (e.g. via winget: winget install jqlang.jq)" -ForegroundColor Yellow
    exit 1
  }
}

Need-Jq
if (-not (Test-Path "$Repo\.git")) { git clone $Remote $Repo }
Set-Location $Repo
if (-not (Test-Path "docs")) { New-Item -ItemType Directory docs | Out-Null }
if (-not (Test-Path "docs\data.json")) { Set-Content -Path "docs\data.json" -Value '{"machines":[]}' -Encoding UTF8 }
if (-not (Test-Path "docs\machine_aliases.json")) { Set-Content -Path "docs\machine_aliases.json" -Value '{}' -Encoding UTF8 }

$MachineId = $env:COMPUTERNAME
$Now = (Get-Date).ToUniversalTime().ToString("s") + "Z"

# alias map
jq --arg id "$MachineId" --arg name "$AliasName" '.[$id]=$name' `
  docs/machine_aliases.json | Out-File -Encoding utf8 docs/machine_aliases.json

# data.json upsert
jq --arg id "$MachineId" --arg now "$Now" --arg st "$Status" --arg name "$AliasName" `
  'def upsert(m): (.machines |= (map(select(.machine_id != m.machine_id)) + [m]));
   if .machines then upsert({"machine_id":$id,"name":$name,"last_update":$now,"status":$st})
   else {"machines":[{"machine_id":$id,"name":$name,"last_update":$now,"status":$st}]} end' `
  docs/data.json | Out-File -Encoding utf8 docs/data.json

git add docs/data.json docs/machine_aliases.json
git commit -m "self-heal: $MachineId ($Status) @ $Now" 2>$null
git pull --rebase --autostash
git push
Write-Host "✓ Updated $MachineId → $AliasName ($Status)" -ForegroundColor Green
