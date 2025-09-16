# OSP9: Setup kms-central-bundle (One-Shot)
param(
  [string]$BaseDir = "C:\KnowingMindSuite"
)

Write-Host "=== KMS Central Bundle Setup ==="

# เตรียมโฟลเดอร์
New-Item -Force -ItemType Directory -Path $BaseDir | Out-Null
Set-Location $BaseDir

# clone หรือ pull repo central
if (-not (Test-Path "$BaseDir\kms-central-bundle\.git")) {
    git clone git@github.com:siango/kms-central-bundle.git
} else {
    Set-Location "$BaseDir\kms-central-bundle"
    git fetch origin
    git pull origin main
}

# sync submodule (audit)
if (-not (Test-Path "$BaseDir\kms-central-bundle\audits\.git")) {
    git submodule add git@github.com:siango/kms-audit.git audits
}
git submodule update --init --recursive

# copy security tools
New-Item -Force -ItemType Directory -Path "$BaseDir\kms-central-bundle\security-tools" | Out-Null
# (ตรงนี้สามารถ copy ไฟล์ baseline เข้าได้เลย)

# verify
Write-Host ">>> Central bundle ready at $BaseDir\kms-central-bundle"
