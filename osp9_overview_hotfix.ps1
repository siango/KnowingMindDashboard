# ================== OSP9 OVERVIEW HOTFIX (safe one-shot) ==================
param(
  [string]$Owner='siango',
  [string]$Repo='KnowingMindDashboard',
  [string]$Branch='gh-pages',
  [string]$SubDir='v2/console'         # web root
)

$ErrorActionPreference='Stop'
function Info($m){Write-Host "[i] $m" -ForegroundColor Cyan}
function Ok($m){Write-Host "[✓] $m" -ForegroundColor Green}
function Warn($m){Write-Host "[!] $m" -ForegroundColor Yellow}

$tsIso = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
$verTag = "v-" + (Get-Date -Format "yyyyMMdd-HHmmss")

# 1) Clone gh-pages → temp (ไม่พึ่งโฟลเดอร์โลคัล เพื่อเลี่ยง path หาย)
$tmp = Join-Path $env:TEMP ("kms_pages_"+(Get-Date -Format "yyyyMMdd-HHmmss"))
$repoUrl = "https://github.com/$Owner/$Repo.git"
Info "Cloning $Owner/$Repo ($Branch) → $tmp"
git clone --branch $Branch --single-branch $repoUrl $tmp | Out-Null
$Root    = $tmp
$WebRoot = Join-Path $Root $SubDir

# 2) ไฟล์สำคัญ
$jsPath  = Join-Path $WebRoot 'assets/js/app.js'
$idxPath = Join-Path $WebRoot 'index.html'
$cssPath = Join-Path $WebRoot 'assets/css/style.css'
if(!(Test-Path $jsPath -PathType Leaf)){ throw "not found: $jsPath" }
if(!(Test-Path $idxPath -PathType Leaf)){ throw "not found: $idxPath" }
if(!(Test-Path $cssPath -PathType Leaf)){ throw "not found: $cssPath" }

# 3) แก้ index.html ให้มี placeholder tabs เหนือ #content
$ix = Get-Content -LiteralPath $idxPath -Raw
if($ix -notmatch 'id="proj-tabs"'){
  $inject = '<div id="proj-tabs" class="proj-tabs glass"></div>'
  # ใช้ RegexOptions.IgnoreCase ที่ถูกต้อง (ไม่ใส่ count/matchTimeout)
  $ix = [regex]::Replace($ix,'</body>',
        ($inject + "`n</body>"),
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
  Set-Content -LiteralPath $idxPath -Encoding UTF8 -Value $ix
  Ok "index.html patched (add #proj-tabs before </body>)."
}else{ Info "index.html already has #proj-tabs." }

# 4) patch app.js ให้ไป hydrate tabs → #proj-tabs (ไม่ยุ่งเมนูซ้าย)
$js = Get-Content -LiteralPath $jsPath -Raw
if($js -notmatch 'KM-OV-PROJ-TABS'){
  $patch = @"
;/* KM-OV-PROJ-TABS */
(async function(){
  const VER = (window.KM_VER||'')+'';
  const holder = document.querySelector('#proj-tabs');
  if(holder){
    try{
      const r = await fetch('./_auto_tabs_block.html'+(VER?('?v='+VER):''),{cache:'no-store'});
      if(r.ok){ holder.innerHTML = await r.text(); }
    }catch(_){}
  }
}());
"@
  Add-Content -Path $jsPath -Value $patch -Encoding UTF8
  Ok "app.js patched (hydrate tabs into #proj-tabs)."
}else{ Info "app.js already patched (KM-OV-PROJ-TABS)." }

# 5) เติม CSS holder สำหรับแถบ tabs (ครั้งเดียว)
if(-not (Select-String -Path $cssPath -Pattern 'KM-OV:TABS-HOLDER' -Quiet)){
  @"
 /* KM-OV:TABS-HOLDER */
 #proj-tabs{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 16px}
 #proj-tabs .nav-link{padding:.4rem .65rem;background:#111827;border:1px solid #1f2937;border-radius:10px}
 #proj-tabs .nav-link.active{border-color:#60a5fa}
"@ | Add-Content -Path $cssPath -Encoding UTF8
  Ok "CSS added for tabs holder."
}else{ Info "CSS holder already present." }

# 6) เขียน version markers อย่างถูกต้อง (ไม่เล่น escape ซ้อน)
$verObj = [ordered]@{ version=$verTag; generated=$tsIso }
$verJson = $verObj | ConvertTo-Json -Compress
Set-Content -Path (Join-Path $Root 'version.json') -Value $verJson -Encoding UTF8
Set-Content -Path (Join-Path $Root ("cache_bust_{0}.txt" -f $verTag)) -Value ("bust {0}" -f $verTag) -Encoding UTF8
Ok "Version marked $verTag"

# 7) commit + push
Push-Location $Root
try{ git add -A | Out-Null }catch{}
try{ git commit -m "[ui/overview] move proj-tabs to #proj-tabs + keep sidebar ($verTag)" | Out-Null }catch{}
git push origin $Branch | Out-Null
Pop-Location
Ok "Pushed to $Branch"

# 8) ลิงก์ทดสอบ
$baseUrl = "https://$Owner.github.io/$Repo"
$sub = ($SubDir -replace '\\','/')
Write-Host ("Overview: {0}/{1}/?v={2}#/overview" -f $baseUrl,$sub,$verTag)
Write-Host ("KMA     : {0}/{1}/?v={2}#/kma" -f $baseUrl,$sub,$verTag)
# ========================================================================
