# AffiliateAgent - update to the latest version from GitHub in one command.
#
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1            # update + restart the app
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1 -Check     # only say whether a new version exists
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1 -NoRestart
#
# Safe: stops if code files were edited on this machine (nothing is overwritten), backs up the
# database first, only fast-forwards (never merges or rewrites), then repairs the libraries
# the new version needs. Data, .env, keys and videos are outside git and are not touched.
param([switch]$Check, [switch]$NoRestart, [switch]$Force)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Can Git (git-scm.com) de cap nhat.' }
}
if (-not (Test-Path (Join-Path $Root '.git'))) { throw 'Thu muc nay khong phai ban git clone: hay git clone https://github.com/dinhtrongddr-dev/Affiliate-Agent.git' }

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
git fetch --quiet origin $branch
$behind = [int](git rev-list --count "HEAD..origin/$branch")
$current = (git log -1 --format='%h %cd' --date=format:'%d/%m %H:%M').Trim()
if ($behind -eq 0) {
    Write-Host "Da la ban moi nhat ($current)." -ForegroundColor Green
    if ($Check) { exit 0 }
    if (-not $Force) { exit 0 }
}
Write-Host "Co $behind cap nhat moi:" -ForegroundColor Cyan
git log --format='  - %s' "HEAD..origin/$branch" | Select-Object -First 15
if ($Check) { exit 0 }

# Never overwrite code someone edited on this machine.
$dirty = git status --porcelain --untracked-files=no
if ($dirty) {
    Write-Host "May nay co file ma nguon bi sua tay:" -ForegroundColor Yellow
    $dirty | ForEach-Object { Write-Host "  $_" }
    throw 'Dung cap nhat de khong ghi de. Sao luu/huy cac thay doi do (git stash) roi chay lai.'
}

# Stop the running app (only this folder's .venv python).
$venvPy = (Join-Path $Root '.venv\Scripts\python.exe').ToLower()
$procs = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -match 'agent_core\.cli' -and ($_.ExecutablePath -and $_.ExecutablePath.ToLower() -like "$($Root.ToLower())*") }
$wasRunning = [bool]$procs
foreach ($p in $procs) { & taskkill /PID $p.ProcessId /T /F 2>$null | Out-Null }
if ($wasRunning) { Write-Host 'Da tat app dang chay.'; Start-Sleep 2 }

# Back up the database before the new version touches it.
$db = Join-Path $Root 'state\affiliate-agent.sqlite3'
if (Test-Path $db) {
    $bk = Join-Path $Root ('backups\affiliate-agent-before-update-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.sqlite3')
    New-Item -ItemType Directory -Force -Path (Split-Path $bk) | Out-Null
    Copy-Item $db $bk -Force   # the app is stopped, so a file copy is consistent
    Get-ChildItem (Split-Path $bk) -Filter 'affiliate-agent-before-update-*.sqlite3' |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 5 | Remove-Item -Force   # keep 5
    Write-Host "Da sao luu CSDL: $bk"
}

$before = (git rev-parse HEAD).Trim()
git merge --ff-only "origin/$branch"
if ($LASTEXITCODE -ne 0) { throw 'Khong cap nhat thang (fast-forward) duoc: lich su da khac nhau, can xu ly tay.' }

# Libraries / tools the new version may need (models are already on this machine).
$changed = git diff --name-only $before HEAD
$needsSetup = $Force -or -not (Test-Path (Join-Path $Root '.venv\Scripts\python.exe')) -or
    ($changed | Where-Object { $_ -match 'requirements|pyproject|scripts/setup\.ps1' })
if ($needsSetup) {
    Write-Host 'Ban moi doi thu vien: dang kiem tra va cai...' -ForegroundColor Cyan
    & powershell -ExecutionPolicy Bypass -File (Join-Path $Root 'scripts\setup.ps1') -SkipModels
} else {
    & (Join-Path $Root '.venv\Scripts\python.exe') -m pip check | Out-Null
    if ($LASTEXITCODE -ne 0) { & powershell -ExecutionPolicy Bypass -File (Join-Path $Root 'scripts\setup.ps1') -SkipModels }
}

$now = (git log -1 --format='%h %cd' --date=format:'%d/%m %H:%M').Trim()
Write-Host "Da cap nhat: $current -> $now" -ForegroundColor Green
if (-not $NoRestart) {
    Start-Process -FilePath (Join-Path $Root 'scripts\start-app.cmd') -WorkingDirectory $Root -WindowStyle Minimized
    Write-Host 'Da mo lai app.'
}
