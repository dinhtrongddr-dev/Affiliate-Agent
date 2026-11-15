# Start ChatGPT2API (the AI gateway) and SAY whether it runs.
# Its own start.cmd only launches a hidden tray icon and exits, so an error stays invisible.
# This waits for the server to answer and, if it does not, prints the reason from its logs.
param([string]$Dir = '')
$ErrorActionPreference = 'Continue'
$AppRoot = Split-Path -Parent $PSScriptRoot
function Stop-WithError { Write-Host ''; Read-Host 'Nhan Enter de dong cua so nay' | Out-Null; exit 1 }

if (-not $Dir) {
    foreach ($c in @((Join-Path (Split-Path -Parent $AppRoot) 'chatgpt2api-web'), 'D:\chatgpt2api-web',
                     'E:\chatgpt2api-web', 'C:\chatgpt2api-web')) {
        if (Test-Path (Join-Path $c 'main.py')) { $Dir = $c; break }
    }
}
if (-not $Dir) { Write-Host 'Khong tim thay thu muc chatgpt2api-web canh app.' -ForegroundColor Red; Stop-WithError }

$port = '13000'
$envFile = Join-Path $Dir '.env'
if (Test-Path $envFile) {
    $l = Get-Content $envFile | Where-Object { $_ -match '^\s*CHATGPT2API_PORT\s*=' } | Select-Object -First 1
    if ($l) { $port = ($l -split '=', 2)[1].Trim() }
}
$url = "http://127.0.0.1:$port"
function Test-Up {
    try { $null = Invoke-WebRequest -UseBasicParsing "$url/v1/models" -TimeoutSec 2; return $true }
    catch { return [bool]$_.Exception.Response }   # 401 = up, just needs the key
}
function Show-Tail($name) {
    $p = Join-Path $Dir $name
    if (Test-Path $p) {
        Write-Host "`n--- 15 dong cuoi $name ---" -ForegroundColor DarkGray
        Get-Content $p -Tail 15 | ForEach-Object { Write-Host "  $_" }
    }
}

Write-Host "ChatGPT2API: $Dir" -ForegroundColor Cyan
if (Test-Up) {
    Write-Host "DANG CHAY tai $url  (giao dien: $url)" -ForegroundColor Green
} else {
    # a half-made .venv shows the "venv launcher is sorry" box and nothing starts
    $venv = Join-Path $Dir '.venv'
    $bundled = Join-Path $Dir 'runtime\python\pythonw.exe'
    if (-not (Test-Path $bundled)) {
        if (-not (Test-Path (Join-Path $venv 'pyvenv.cfg')) -or -not (Test-Path (Join-Path $venv 'Scripts\pythonw.exe'))) {
            Write-Host 'LOI: .venv cua ChatGPT2API bi hong hoac chua cai.' -ForegroundColor Red
            Write-Host "Sua: chay  powershell -ExecutionPolicy Bypass -File `"$Dir\setup.ps1`"  roi mo lai loi tat nay." -ForegroundColor Yellow
            Stop-WithError
        }
    }
    Write-Host 'Dang khoi dong (icon o khay he thong)...'
    Start-Process -FilePath (Join-Path $Dir 'start.cmd') -WorkingDirectory $Dir -WindowStyle Hidden
    $up = $false
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (-not $up -and $clock.Elapsed.TotalSeconds -lt 60) { Start-Sleep 2; $up = Test-Up }
    if ($up) {
        Write-Host "DANG CHAY tai $url  (giao dien: $url)" -ForegroundColor Green
    } else {
        Write-Host "LOI: sau 60 giay server van chua tra loi o $url" -ForegroundColor Red
        Show-Tail 'tray.log'
        Show-Tail 'service.log'
        Write-Host "`nThuong gap: thieu thu vien (chay $Dir\setup.ps1), cong $port bi chuong trinh khac chiem," -ForegroundColor Yellow
        Write-Host 'hoac Windows Defender chan python. Gui anh cua so nay neu can ho tro.' -ForegroundColor Yellow
        Stop-WithError
    }
}

# The Muse video gateway is a different thing: it lives inside the app and only runs while the app is open.
try { $null = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:18620/v1/models' -TimeoutSec 3; $muse = $true }
catch { $muse = [bool]$_.Exception.Response }
if ($muse) { Write-Host 'Gateway Muse (video AI): dang chay tai http://127.0.0.1:18620' -ForegroundColor Green }
else { Write-Host 'Gateway Muse (video AI): chi chay khi app Affiliate Agent dang mo (tu bat o cong 18620).' -ForegroundColor DarkGray }
Write-Host "`nMoi thu on: cua so tu dong sau 4 giay (ChatGPT2API van chay o khay he thong)." -ForegroundColor DarkGray
Start-Sleep 4
exit 0
