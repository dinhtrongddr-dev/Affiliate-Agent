# AffiliateAgent - one-command setup / repair for a Windows machine.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Import D:\affiliate-export.zip
#
# Safe to run again at any time: every step checks first and only installs or repairs what is
# missing (Python 3.12, ffmpeg, the .venv packages, Playwright Chromium, the VieNeu voice model).
# Keys and logins are never copied between machines (Windows DPAPI): the end of the run lists
# what to enter again in Settings.
param(
    [string]$ChatGPT2ApiDir = '',
    [string]$MediaRoot = '',
    [string]$Import = '',
    [switch]$SkipModels,
    [switch]$Dev
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$ok = [System.Collections.Generic.List[string]]::new()
$todo = [System.Collections.Generic.List[string]]::new()

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Has-Winget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }

# ------------------------------------------------------------------ Python 3.12+
Step 'Python'
function Find-Python {
    foreach ($cmd in @(@('py', '-3.12'), @('py', '-3'), @('python'))) {
        try {
            $exe = $cmd[0]; $pre = @($cmd | Select-Object -Skip 1)
            $ver = & $exe @pre -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
            if ($ver -and [version]$ver -ge [version]'3.12') {
                return (& $exe @pre -c "import sys;print(sys.executable)")
            }
        } catch {}
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    if (-not (Has-Winget)) { throw 'Can Python 3.12+ (python.org) hoac winget de cai tu dong.' }
    Write-Host 'Chua co Python 3.12+: dang cai qua winget...' -ForegroundColor Yellow
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $py = Find-Python
    if (-not $py) { throw 'Da cai Python nhung chua thay trong PATH: mo PowerShell moi va chay lai.' }
}
$ok.Add("Python: $py")

# ------------------------------------------------------------------ ffmpeg / ffprobe
Step 'ffmpeg'
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    if (-not (Has-Winget)) { throw 'Can ffmpeg (ffmpeg.org) hoac winget de cai tu dong.' }
    Write-Host 'Chua co ffmpeg: dang cai qua winget...' -ForegroundColor Yellow
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    Refresh-Path
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
        throw 'Da cai ffmpeg nhung chua thay trong PATH: mo PowerShell moi va chay lai.'
    }
}
$ok.Add('ffmpeg: ' + (& ffmpeg -hide_banner -version | Select-Object -First 1))

# ------------------------------------------------------------------ .venv + packages
Step 'Thu vien Python (.venv)'
$venvPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) { & $py -m venv (Join-Path $Root '.venv') }
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r (Join-Path $Root 'services\requirements-agent.txt') requests websocket-client --quiet
if ($Dev) { & $venvPy -m pip install pytest ruff --quiet }
& $venvPy -m pip check
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Phat hien thu vien loi phien ban: cai lai...' -ForegroundColor Yellow
    & $venvPy -m pip install -r (Join-Path $Root 'services\requirements-agent.txt') --force-reinstall --quiet
}
$env:PYTHONPATH = Join-Path $Root 'services'
& $venvPy -c "import fastapi, uvicorn, httpx, watchdog, rapidfuzz, pydantic_settings, playwright, jinja2, soundfile, vieneu, requests, websocket; import agent_core.web.app; print('import OK')"
if ($LASTEXITCODE -ne 0) { throw 'Thu vien chua dung: xem loi o tren.' }
$ok.Add('Thu vien Python: OK')

# Playwright browser (Shopee Creator login)
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Root 'cache\playwright'   # same place start-app.cmd uses
& $venvPy -m playwright install chromium | Out-Null
$ok.Add('Playwright Chromium: OK')

# Chrome/Edge for the embedded Muse gateway
& $venvPy -c "from agent_core.videogen.gateway import find_chrome; import sys; sys.exit(0 if find_chrome() else 1)"
if ($LASTEXITCODE -eq 0) { $ok.Add('Chrome/Edge cho Muse: OK') } else { $todo.Add('Cai Google Chrome hoac Microsoft Edge (gateway Muse can trinh duyet).') }

# ------------------------------------------------------------------ .env
Step 'Cau hinh (.env)'
$envPath = Join-Path $Root '.env'
if (-not (Test-Path $envPath)) { Copy-Item (Join-Path $Root 'config\.env.example') $envPath }
function Set-EnvValue($key, $value) {
    $lines = [System.Collections.Generic.List[string]](Get-Content $envPath -Encoding UTF8)
    $idx = -1
    for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^\s*$([regex]::Escape($key))\s*=") { $idx = $i } }
    if ($idx -ge 0) { $lines[$idx] = "$key=$value" } else { $lines.Add("$key=$value") }
    [System.IO.File]::WriteAllLines($envPath, $lines, (New-Object System.Text.UTF8Encoding($false)))
}
function Get-EnvValue($key) {
    $line = Get-Content $envPath -Encoding UTF8 | Where-Object { $_ -match "^\s*$([regex]::Escape($key))\s*=" } | Select-Object -Last 1
    if ($line) { return ($line -split '=', 2)[1].Trim() } return ''
}

# Media folder: keep the configured one if its drive exists, else next to the app.
$media = if ($MediaRoot) { $MediaRoot } else { Get-EnvValue 'AA_MEDIA_ROOT' }
if (-not $media -or -not (Test-Path (Split-Path -Qualifier $media))) {
    $media = Join-Path (Split-Path -Parent $Root) 'AffiliateAgentMedia'
}
Set-EnvValue 'AA_MEDIA_ROOT' $media
New-Item -ItemType Directory -Force -Path $media | Out-Null
$ok.Add("Thu muc video: $media")

# ChatGPT2API: reuse its auth key (never printed).
if (-not $ChatGPT2ApiDir) {
    foreach ($c in @((Join-Path (Split-Path -Parent $Root) 'chatgpt2api-web'), 'D:\chatgpt2api-web', 'C:\chatgpt2api-web')) {
        if (Test-Path (Join-Path $c '.env')) { $ChatGPT2ApiDir = $c; break }
    }
}
if ($ChatGPT2ApiDir -and (Test-Path (Join-Path $ChatGPT2ApiDir '.env'))) {
    $line = Get-Content (Join-Path $ChatGPT2ApiDir '.env') -Encoding UTF8 | Where-Object { $_ -match '^\s*CHATGPT2API_AUTH_KEY\s*=' } | Select-Object -First 1
    $port = (Get-Content (Join-Path $ChatGPT2ApiDir '.env') -Encoding UTF8 | Where-Object { $_ -match '^\s*CHATGPT2API_PORT\s*=' } | Select-Object -First 1)
    $port = if ($port) { ($port -split '=', 2)[1].Trim() } else { '13000' }
    if ($line) {
        Set-EnvValue 'AA_AI_API_KEY' (($line -split '=', 2)[1].Trim().Trim('"').Trim("'"))
        Set-EnvValue 'AA_AI_BASE_URL' "http://127.0.0.1:$port/v1"
        Set-EnvValue 'AA_AI_PROTOCOL' 'openai'
        $ok.Add("ChatGPT2API: $ChatGPT2ApiDir (port $port, key da noi)")
    }
} else {
    $todo.Add('Cai ChatGPT2API: git clone https://github.com/dinhtrongddr-dev/chatgpt2api-web, chay setup.ps1 trong do, roi chay lai script nay (hoac them -ChatGPT2ApiDir).')
}

# ------------------------------------------------------------------ import an export package
if ($Import) {
    Step "Nhap du lieu tu $Import"
    $tmp = Join-Path $env:TEMP ('aa-import-' + [guid]::NewGuid())
    Expand-Archive -Path $Import -DestinationPath $tmp
    $db = Join-Path $Root 'state\affiliate-agent.sqlite3'
    if (Test-Path $db) { Copy-Item $db ($db + '.before-import') -Force }
    New-Item -ItemType Directory -Force -Path (Join-Path $Root 'state') | Out-Null
    Copy-Item (Join-Path $tmp 'state\affiliate-agent.sqlite3') $db -Force
    if (Test-Path (Join-Path $tmp 'config')) { Copy-Item (Join-Path $tmp 'config\*') (Join-Path $Root 'config') -Recurse -Force }
    if (Test-Path (Join-Path $tmp 'settings.env')) {
        foreach ($l in Get-Content (Join-Path $tmp 'settings.env') -Encoding UTF8) {
            if ($l -match '^\s*(AA_[A-Z0-9_]+)\s*=(.*)$' -and $Matches[1] -notmatch 'MEDIA_ROOT|AI_API_KEY|AI_BASE_URL') {
                Set-EnvValue $Matches[1] $Matches[2].Trim()
            }
        }
    }
    foreach ($sub in @('assets', 'outbox')) {
        if (Test-Path (Join-Path $tmp "media\$sub")) {
            Copy-Item (Join-Path $tmp "media\$sub") $media -Recurse -Force
        }
    }
    Remove-Item $tmp -Recurse -Force
    $ok.Add('Da nhap co so du lieu, cau hinh va tai san tu goi xuat')
}

# ------------------------------------------------------------------ models + GPU
if (-not $SkipModels) {
    Step 'Model giong VieNeu (tai lan dau ~0,8 GB)'
    & $venvPy -c "from agent_core.editor.tts import get_engine; get_engine('fast'); print('VieNeu OK')"
    if ($LASTEXITCODE -eq 0) { $ok.Add('Model giong VieNeu: OK') } else { $todo.Add('Model giong VieNeu chua tai duoc: kiem tra mang roi chay lai.') }
}
& $venvPy -c "from agent_core.editor.encoder import nvenc_available; import sys; sys.exit(0 if nvenc_available('ffmpeg') else 1)"
if ($LASTEXITCODE -eq 0) { $ok.Add('Ma hoa GPU (NVENC): co') } else { $ok.Add('Ma hoa GPU (NVENC): khong co, dung x264 (cham hon, van chay)') }

# ------------------------------------------------------------------ smoke check + report
& $venvPy -m agent_core.cli --help | Out-Null
$todo.Add('Mo app (scripts\start-app.cmd) > Cai dat: nhap lai cac key luu bang Windows (DPAPI) - Muse cookie JSON, AddLive API key, Facebook token, YouTube/Trends-MCP key neu dung.')
$todo.Add('Dang nhap lai Shopee Creator: scripts\open-shopee-login.cmd')

Write-Host "`n================ KET QUA ================" -ForegroundColor Green
$ok | ForEach-Object { Write-Host "  [OK] $_" -ForegroundColor Green }
Write-Host "`nCon can lam:" -ForegroundColor Yellow
$todo | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
