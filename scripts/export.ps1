# AffiliateAgent - pack what another machine needs into one small zip.
#
#   powershell -ExecutionPolicy Bypass -File scripts\export.ps1                 # data only (a few MB)
#   powershell -ExecutionPolicy Bypass -File scripts\export.ps1 -IncludeVideos  # + finished videos
#
# Contains: a consistent copy of the database, settings (.env WITHOUT keys/tokens), config files,
# series reference images. Never contains: keys, tokens, cookies, logins (they are encrypted
# for this Windows user only), the .venv, caches. Code comes from git.
param(
    [string]$Out = '',
    [switch]$IncludeVideos
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not $Out) { $Out = Join-Path (Split-Path -Parent $Root) ("affiliate-export-" + (Get-Date -Format 'yyyyMMdd-HHmm') + '.zip') }
$tmp = Join-Path $env:TEMP ('aa-export-' + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path (Join-Path $tmp 'state') | Out-Null

# 1) database: SQLite online backup (safe while the app is running)
$db = Join-Path $Root 'state\affiliate-agent.sqlite3'
& $py -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" $db (Join-Path $tmp 'state\affiliate-agent.sqlite3')

# 2) settings without secrets
$secret = 'KEY|TOKEN|SECRET|PASSWORD|COOKIE'
$envPath = Join-Path $Root '.env'
if (Test-Path $envPath) {
    Get-Content $envPath -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^\s*([A-Z0-9_]+)\s*=' -and $Matches[1] -match $secret) { "$($Matches[1])=" } else { $_ }
    } | Set-Content -Encoding UTF8 (Join-Path $tmp 'settings.env')
}

# 3) config files (pronunciations, series templates ...), minus local credential files
if (Test-Path (Join-Path $Root 'config')) {
    Copy-Item (Join-Path $Root 'config') (Join-Path $tmp 'config') -Recurse
    Get-ChildItem (Join-Path $tmp 'config') -Recurse -File |
        Where-Object { $_.Name -match 'credential|secret|token|\.env$' } | Remove-Item -Force
}

# 4) media: series/character reference images always, finished videos on request
$media = (Get-Content $envPath -Encoding UTF8 | Where-Object { $_ -match '^\s*AA_MEDIA_ROOT\s*=' } | Select-Object -Last 1)
$media = if ($media) { ($media -split '=', 2)[1].Trim() } else { 'E:\AffiliateAgentMedia' }
foreach ($sub in @('creative_series', 'music')) {   # small, keeps series characters and own music
    $src = Join-Path $media "assets\$sub"
    if (Test-Path $src) { Copy-Item $src (Join-Path $tmp "media\assets\$sub") -Recurse }
}
if ($IncludeVideos) {   # AI clip library + finished videos (hundreds of MB)
    if (Test-Path (Join-Path $media 'assets\clips')) { Copy-Item (Join-Path $media 'assets\clips') (Join-Path $tmp 'media\assets\clips') -Recurse }
    if (Test-Path (Join-Path $media 'outbox')) { Copy-Item (Join-Path $media 'outbox') (Join-Path $tmp 'media\outbox') -Recurse }
}

@"
Goi xuat AffiliateAgent $(Get-Date -Format 'yyyy-MM-dd HH:mm')
Tren may moi:
  1. git clone (hoac chep) ma nguon AffiliateAgent va chatgpt2api-web
  2. chatgpt2api-web\setup.ps1, roi start.cmd va dang nhap ChatGPT
  3. powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Import <file zip nay>
  4. Mo app > Cai dat: nhap lai key (Muse cookie, AddLive, Facebook...) - key khong duoc chep sang.
"@ | Set-Content -Encoding UTF8 (Join-Path $tmp 'README.txt')

if (Test-Path $Out) { Remove-Item $Out -Force }
Compress-Archive -Path (Join-Path $tmp '*') -DestinationPath $Out
Remove-Item $tmp -Recurse -Force
$size = [math]::Round((Get-Item $Out).Length / 1MB, 1)
Write-Host "Da xuat: $Out ($size MB)" -ForegroundColor Green
