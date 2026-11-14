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
    [switch]$SkipChatGPT2Api,
    [switch]$Dev
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'logs') | Out-Null
$SetupLog = Join-Path $Root ('logs\setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
try { Start-Transcript -Path $SetupLog -Force | Out-Null } catch {}
trap {
    Write-Host "`nLOI: $_" -ForegroundColor Red
    Write-Host "Nhat ky day du: $SetupLog (gui file nay neu can ho tro). Sua loi roi chay lai setup (an toan)." -ForegroundColor Yellow
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}
$ok = [System.Collections.Generic.List[string]]::new()
$todo = [System.Collections.Generic.List[string]]::new()

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Has-Winget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }
function Repair-Venv($venvDir) {
    # A half-made or copied .venv (no pyvenv.cfg, or its base Python is gone) makes python.exe pop up
    # "Python venv launcher is sorry to say ... failed to locate pyvenv.cfg" and hang the script.
    # Check the files first - never run a broken launcher - and delete the .venv so it is rebuilt.
    if (-not (Test-Path $venvDir)) { return }
    $cfg = Join-Path $venvDir 'pyvenv.cfg'
    $why = ''
    if (-not (Test-Path (Join-Path $venvDir 'Scripts\python.exe'))) { $why = 'thieu python.exe' }
    elseif (-not (Test-Path $cfg)) { $why = 'thieu pyvenv.cfg' }
    else {
        $home_ = (Get-Content $cfg | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1)
        $home_ = if ($home_) { ($home_ -split '=', 2)[1].Trim() } else { '' }
        if (-not $home_ -or -not (Test-Path -LiteralPath ($home_.TrimEnd('\') + '\python.exe'))) { $why = "Python goc ($home_) khong con" }
    }
    if ($why) {
        Write-Host "$venvDir bi hong ($why): xoa de tao lai..." -ForegroundColor Yellow
        Get-Process python*, pythonw* -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path.StartsWith($venvDir, [StringComparison]::OrdinalIgnoreCase) } |
            Stop-Process -Force -ErrorAction SilentlyContinue
        Remove-Item $venvDir -Recurse -Force
    }
}

# ------------------------------------------------------------------ Python 3.12+
Step 'Python'
function Find-Python {
    # 3.12 or 3.13 only: newer Python may lack wheels for the voice engine (VieNeu/onnxruntime)
    foreach ($cmd in @(@('py', '-3.12'), @('py', '-3.13'), @('python'))) {
        try {
            $exe = $cmd[0]; $pre = @($cmd | Select-Object -Skip 1)
            $ver = & $exe @pre -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
            if ($ver -and @('3.12', '3.13') -contains "$ver".Trim()) {
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

# ------------------------------------------------------------------ Visual C++ runtime
# The voice engine (onnxruntime) needs VC++ 2015-2022 runtime 14.40+; an older one makes the app
# crash with 0xC0000005 (exit -1073741819) while loading the voice model.
Step 'Microsoft Visual C++ Runtime'
function Get-VCVersion {
    foreach ($k in @('HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
                     'HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64')) {
        try {
            $r = Get-ItemProperty $k -ErrorAction Stop
            if ($r.Installed -eq 1) { return [version]("{0}.{1}.{2}" -f $r.Major, $r.Minor, $r.Bld) }
        } catch {}
    }
    return $null
}
$vc = Get-VCVersion
if (-not $vc -or $vc -lt [version]'14.40.0') {
    Write-Host "VC++ Runtime $(if ($vc) { $vc } else { 'chua co' }): dang cai ban moi (co the hien hop thoai xin quyen Admin, bam Yes)..." -ForegroundColor Yellow
    $done = $false
    if (Has-Winget) {
        winget install -e --id Microsoft.VCRedist.2015+.x64 --accept-package-agreements --accept-source-agreements --silent
        $done = (Get-VCVersion) -ge [version]'14.40.0'
    }
    if (-not $done) {
        $vcExe = Join-Path $env:TEMP 'vc_redist.x64.exe'
        Invoke-WebRequest 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $vcExe -UseBasicParsing
        Start-Process $vcExe -ArgumentList '/install', '/quiet', '/norestart' -Verb RunAs -Wait
        $done = (Get-VCVersion) -ge [version]'14.40.0'
    }
    $vc = Get-VCVersion
    if ($done) { $ok.Add("VC++ Runtime: $vc") }
    else { $todo.Add('Cai Microsoft Visual C++ Runtime: https://aka.ms/vs/17/release/vc_redist.x64.exe (thieu thi giong doc se loi).') }
} else {
    $ok.Add("VC++ Runtime: $vc")
}

# ------------------------------------------------------------------ .venv + packages
Step 'Thu vien Python (.venv)'
$venvPy = Join-Path $Root '.venv\Scripts\python.exe'
Repair-Venv (Join-Path $Root '.venv')
if (Test-Path $venvPy) {   # an old .venv made by an unsupported Python is rebuilt
    $vv = (& $venvPy -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null)
    if (@('3.12', '3.13') -notcontains "$vv".Trim()) {
        Write-Host ".venv dung Python $vv khong ho tro: tao lai..." -ForegroundColor Yellow
        Remove-Item (Join-Path $Root '.venv') -Recurse -Force
    }
}
if (-not (Test-Path $venvPy)) { & $py -m venv (Join-Path $Root '.venv') }
if (-not (Test-Path $venvPy)) { throw "Khong tao duoc .venv bang $py" }
& $venvPy -m pip install --upgrade pip --quiet
Write-Host 'Dang cai thu vien (lan dau co the mat 5-15 phut, dung tat cua so)...'
& $venvPy -m pip install -r (Join-Path $Root 'services\requirements-agent.txt') requests websocket-client --quiet
if ($LASTEXITCODE -ne 0) { throw 'pip install loi (xem dong loi ngay phia tren). Kiem tra mang roi chay lai setup.' }
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

# Video AI: the Muse gateway (muse2api) ships with the app and runs as a child process on
# 127.0.0.1:18620 while the app is open. A fresh .env says "none", so it never started.
$vp = (Get-EnvValue 'AA_VIDEOGEN_PROVIDER').ToLower()
if (-not $vp -or $vp -eq 'none') { Set-EnvValue 'AA_VIDEOGEN_PROVIDER' 'muse2api'; $vp = 'muse2api' }
if ($vp -eq 'muse2api') {
    $emb = (Get-EnvValue 'AA_MUSE_EMBEDDED').ToLower()
    if ($emb -in @('false', '0', 'no')) {
        $ok.Add('Video AI: muse2api ngoai (AA_MUSE_EMBEDDED=false) - tu chay gateway cua ban')
    } else {
        $ok.Add('Video AI: Muse (gateway kem app, cong 18620, tu chay khi mo app)')
    }
}

# ------------------------------------------------------------------ ChatGPT2API (AI gateway)
Step 'ChatGPT2API (AI viet kich ban)'
$C2AUrl = 'https://github.com/dinhtrongddr-dev/chatgpt2api-web.git'
if (-not $ChatGPT2ApiDir) {
    foreach ($c in @((Join-Path (Split-Path -Parent $Root) 'chatgpt2api-web'), 'D:\chatgpt2api-web', 'C:\chatgpt2api-web')) {
        if (Test-Path (Join-Path $c 'main.py')) { $ChatGPT2ApiDir = $c; break }
    }
}
if (-not $ChatGPT2ApiDir -and -not $SkipChatGPT2Api) {
    # not on this machine yet: clone it next to the app
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        if (-not (Has-Winget)) { throw 'Can Git hoac winget de tai ChatGPT2API.' }
        winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
        Refresh-Path
    }
    $ChatGPT2ApiDir = Join-Path (Split-Path -Parent $Root) 'chatgpt2api-web'
    Write-Host "Dang tai ChatGPT2API vao $ChatGPT2ApiDir ..."
    git clone --quiet $C2AUrl $ChatGPT2ApiDir
}
if ($ChatGPT2ApiDir -and (Test-Path (Join-Path $ChatGPT2ApiDir 'main.py'))) {
    # its own setup: Python 3.13 + packages + .env with a random key (repairs if needed)
    Repair-Venv (Join-Path $ChatGPT2ApiDir '.venv')
    $c2aPy =@((Join-Path $ChatGPT2ApiDir 'runtime\python\python.exe'), (Join-Path $ChatGPT2ApiDir '.venv\Scripts\python.exe')) |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    $c2aOk = $false
    if ($c2aPy -and (Test-Path (Join-Path $ChatGPT2ApiDir '.env'))) {
        & $c2aPy -c "import fastapi, uvicorn, curl_cffi, sqlalchemy" 2>$null
        $c2aOk = ($LASTEXITCODE -eq 0)
    }
    if (-not $c2aOk) {
        & powershell -ExecutionPolicy Bypass -File (Join-Path $ChatGPT2ApiDir 'setup.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Cai ChatGPT2API loi: xem thong bao o tren.' }
    }
    $envC = Join-Path $ChatGPT2ApiDir '.env'
    $line = Get-Content $envC -Encoding UTF8 | Where-Object { $_ -match '^\s*CHATGPT2API_AUTH_KEY\s*=' } | Select-Object -First 1
    $port = Get-Content $envC -Encoding UTF8 | Where-Object { $_ -match '^\s*CHATGPT2API_PORT\s*=' } | Select-Object -First 1
    $port = if ($port) { ($port -split '=', 2)[1].Trim() } else { '13000' }
    if ($line) {
        Set-EnvValue 'AA_AI_API_KEY' (($line -split '=', 2)[1].Trim().Trim('"').Trim("'"))   # never printed
        Set-EnvValue 'AA_AI_BASE_URL' "http://127.0.0.1:$port/v1"
        Set-EnvValue 'AA_AI_PROTOCOL' 'openai'
    }
    # start it if it is not running, then wait until it answers
    function Test-C2A { try { $null = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/v1/models" -TimeoutSec 5; return $true } catch { return [bool]($_.Exception.Response) } }
    if (-not (Test-C2A)) {
        Start-Process -FilePath (Join-Path $ChatGPT2ApiDir 'start.cmd') -WorkingDirectory $ChatGPT2ApiDir -WindowStyle Hidden
        for ($i = 0; $i -lt 45 -and -not (Test-C2A); $i++) { Start-Sleep 2 }
    }
    if (Test-C2A) { $ok.Add("ChatGPT2API: $ChatGPT2ApiDir dang chay tai 127.0.0.1:$port (key da noi)") }
    else { $todo.Add("ChatGPT2API chua chay: mo loi tat 'ChatGPT2API' tren Desktop (no in ra ly do loi), sua roi chay lai setup.") }
    # shortcut to start it later
    $script:ChatGPT2ApiStart = Join-Path $Root 'scripts\start-chatgpt2api.cmd'   # starts it AND reports OK / the error
} else {
    $todo.Add("Cai ChatGPT2API: git clone $C2AUrl, chay setup.ps1 trong do, roi chay lai script nay.")
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
    if ($LASTEXITCODE -eq 0) { $ok.Add('Model giong VieNeu: OK') } else { $todo.Add('Giong VieNeu chua chay duoc (xem dong loi o tren): kiem tra mang / cai VC++ Runtime https://aka.ms/vs/17/release/vc_redist.x64.exe roi chay lai setup. App van mo duoc, chi tam khong co giong doc.') }
}
& $venvPy -c "from agent_core.editor.encoder import nvenc_available; import sys; sys.exit(0 if nvenc_available('ffmpeg') else 1)"
if ($LASTEXITCODE -eq 0) { $ok.Add('Ma hoa GPU (NVENC): co') } else { $ok.Add('Ma hoa GPU (NVENC): khong co, dung x264 (cham hon, van chay)') }

# Muse gateway: start it once to prove it runs (stopped again; the app starts it itself)
Step 'Gateway Muse (video AI)'
& $venvPy (Join-Path $Root 'scripts\check_muse.py')
if ($LASTEXITCODE -eq 0) { $ok.Add('Gateway Muse: khoi dong thu OK') }
else { $todo.Add("Gateway Muse chua chay duoc: xem $Root\logs\muse-gateway.log (can Chrome/Edge), roi chay lai setup.") }

# ------------------------------------------------------------------ shortcuts
Step 'Loi tat tren Desktop'
try {
    $shell = New-Object -ComObject WScript.Shell
    $desk = [Environment]::GetFolderPath('Desktop')
    $items = @(
        @{ Name = 'Affiliate Agent'; Target = (Join-Path $Root 'scripts\start-app.cmd'); Icon = (Join-Path $Root 'assets\app.ico') },
        @{ Name = 'Cap nhat Affiliate Agent'; Target = (Join-Path $Root 'scripts\update.cmd'); Icon = "$env:SystemRoot\System32\shell32.dll,238" }
    )
    if ($script:ChatGPT2ApiStart) { $items += @{ Name = 'ChatGPT2API'; Target = $script:ChatGPT2ApiStart } }
    foreach ($it in $items) {
        $lnk = $shell.CreateShortcut((Join-Path $desk ($it.Name + '.lnk')))
        # cmd /k keeps the window open, so any error stays readable
        $lnk.TargetPath = "$env:SystemRoot\System32\cmd.exe"
        $lnk.Arguments = '/k "' + $it.Target + '"'
        $lnk.WorkingDirectory = Split-Path -Parent $it.Target
        if ($it.Icon -and (Test-Path ($it.Icon -replace ',\d+$', ''))) { $lnk.IconLocation = $it.Icon }
        $lnk.Description = $it.Name
        $lnk.Save()
    }
    $ok.Add('Loi tat Desktop: ' + (($items | ForEach-Object { $_.Name }) -join ', '))
} catch { $todo.Add('Khong tao duoc loi tat Desktop (khong anh huong app).') }

# ------------------------------------------------------------------ smoke check + report
& $venvPy -m agent_core.cli --help | Out-Null
# a real AI round trip: proves the key, the gateway and a logged-in ChatGPT account
& $venvPy (Join-Path $Root 'scripts\check_ai.py')
if ($LASTEXITCODE -eq 0) { $ok.Add('Goi thu AI (ChatGPT2API): OK') }
else { $todo.Add('Dang nhap tai khoan ChatGPT trong ChatGPT2API (mo http://127.0.0.1:13000), roi chay lai setup de kiem tra.') }
$todo.Add('Mo app (scripts\start-app.cmd) > Cai dat: nhap lai cac key luu bang Windows (DPAPI) - Muse cookie JSON, AddLive API key, Facebook token, YouTube/Trends-MCP key neu dung.')
$todo.Add('Dang nhap lai Shopee Creator: scripts\open-shopee-login.cmd')

Write-Host "`n================ KET QUA ================" -ForegroundColor Green
$ok | ForEach-Object { Write-Host "  [OK] $_" -ForegroundColor Green }
Write-Host "`nCon can lam:" -ForegroundColor Yellow
$todo | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
Write-Host "`nNhat ky: $SetupLog"
try { Stop-Transcript | Out-Null } catch {}
