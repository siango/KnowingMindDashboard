Param(
    # Default output directory.  The Join-Path call must be wrapped in
    # parentheses to form a single expression.  Without parentheses
    # PowerShell attempts to assign a function call directly and fails.
    [string]$OutDir = (Join-Path $env:USERPROFILE "KMS_CDH\snapshots")
)

# -----------------------------------------------------------------------------
#  OSP9 Snapshot Generator
#
#  This script creates a set of placeholder snapshot files used by the KMS
#  Central Data Hub (CDH).  It writes a handful of JSON and CSV files into
#  `$OutDir`.  Each file has a stable filename so that tools (like ChatGPT)
#  can poll the folder for updates.  You can customise `$OutDir` by
#  specifying a different path when calling the script, for example:
#
#      .\osp9_generate_snapshots.ps1 -OutDir "D:\Data\KMS_CDH\snapshots"
#
#  The generated files are:
#    - kms_snapshot_latest.json: summary of project and service status
#    - kms_tasks_latest.json: list of tasks (empty by default)
#    - kms_checks_latest.csv: checklist items (empty by default)
#    - kms_heartbeat_latest.json: list of devices/services heartbeat
#    - kms_keys_meta_latest.json: key metadata (no secrets)
#    - kms_releases_latest.md: marker indicating when this snapshot was created
#
#  The script uses only plain ASCII characters to avoid issues with encodings
#  when executed on systems that restrict non‑signed scripts or exotic
#  Unicode characters.  It does not require elevated privileges or
#  modifications to system execution policies.
# -----------------------------------------------------------------------------

function Ensure-Directory {
    param(
        [string]$Path
    )
    if (-not (Test-Path -Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

# Ensure target directory exists
Ensure-Directory -Path $OutDir

$now = Get-Date -Format s # ISO 8601 without timezone (yyyy-MM-ddTHH:mm:ss)

# -----------------------------------------------------------------------------
# kms_snapshot_latest.json
#
# Contains high‑level summary of projects, services and totals.
# Projects and services arrays are empty here—you can populate them via
# other tools or workflows.  The `totals` object tracks counts.
$snapshot = [ordered]@{
    schema       = "kms_snapshot.v1"
    generated_at = $now
    projects     = @()
    services     = @()
    totals       = [ordered]@{
        tasks_all  = 0
        tasks_done = 0
        issues_open= 0
    }
}
$snapshot | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -Path (Join-Path $OutDir "kms_snapshot_latest.json")

# -----------------------------------------------------------------------------
# kms_tasks_latest.json
$tasks = [ordered]@{
    schema = "kms_tasks.v1"
    items  = @()
}
$tasks | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -Path (Join-Path $OutDir "kms_tasks_latest.json")

# -----------------------------------------------------------------------------
# kms_checks_latest.csv
#
# Provide a header line for CSV.  Additional rows can be appended by other
# scripts or processes.
"item,status,owner,updated_at,notes" | Set-Content -Encoding UTF8 -Path (Join-Path $OutDir "kms_checks_latest.csv")

# -----------------------------------------------------------------------------
# kms_heartbeat_latest.json
$heartbeat = [ordered]@{
    schema = "kms_heartbeat.v1"
    items  = @()
}
$heartbeat | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -Path (Join-Path $OutDir "kms_heartbeat_latest.json")

# -----------------------------------------------------------------------------
# kms_keys_meta_latest.json
$keysMeta = [ordered]@{
    schema = "kms_keys_meta.v1"
    items  = @()
}
$keysMeta | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -Path (Join-Path $OutDir "kms_keys_meta_latest.json")

# -----------------------------------------------------------------------------
# kms_releases_latest.md
#
# A simple markdown file indicating when this snapshot set was generated.
"# $now local snapshot generated" | Set-Content -Encoding UTF8 -Path (Join-Path $OutDir "kms_releases_latest.md")

Write-Host "Snapshots generated in $OutDir" -ForegroundColor Green