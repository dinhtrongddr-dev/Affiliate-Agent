# AffiliateAgent - update to the latest version from GitHub in one command.
#
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1            # update + restart the app
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1 -Check     # only say whether a new version exists
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1 -NoRestart
#
# Safe: stops if code files were edited on this machine (nothing is overwritten), backs up the
# database first, only fast-forwards (never merges or rewrites), then repairs the libraries
# the new version needs. Data, .env, keys and videos are outside git and are not touched.
#   ... -LogFile logs\update-x.log   # started by the app's "Cap nhat" button: log to a file, and
#                                      always reopen the app (also after an error) so it can report
param([switch]$Check, [switch]$NoRestart, [switch]$Force, [string]$LogFile = '')
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$ResultFile = Join-Path $Root 'logs\update-last.json'
$script:From = ''
if ($LogFile) {
    New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
    try { Start-Transcript -Path $LogFile -Force | Out-Null } catch {}
}
function Write-Result([bool]$ok, [string]$message) {
    if ($Check) { return }
    $to = ''
    try { $to = (git log -1 --format='%h' 2>$null).Trim() } catch {}
    $data = @{ ok = $ok; message = $message; from = $script:From; to = $to; log = $LogFile;
               at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }
    try {
        New-Item -ItemType Directory -Force -Path (Split-Path $ResultFile) | Out-Null
        [System.IO.File]::WriteAllText($ResultFile, ($data | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    } catch {}
}
function Start-App {
    $pyw = Join-Path $Root '.venv\Scripts\pythonw.exe'
    if (Test-Path $pyw) {   # background + tray icon, no console window
        Start-Process -FilePath $pyw -ArgumentList '-m', 'agent_core.tray' -WorkingDirectory (Join-Path $Root 'services')
    } else {
        Start-Process -FilePath (Join-Path $Root 'scripts\start-app.cmd') -WorkingDirectory $Root -WindowStyle Minimized
    }
}
function Stop-App {
    # Stop the running app (only this folder's .venv python); tray first: no 'app stopped' alarm
    $procs = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -match 'agent_core\.(cli|tray)' -and ($_.ExecutablePath -and $_.ExecutablePath.ToLower() -like "$($Root.ToLower())*") } |
        Sort-Object { if ($_.CommandLine -match 'agent_core\.tray') { 0 } else { 1 } }
    # /T already ends the children, so a later PID may be gone: taskkill then writes to stderr,
    # which PowerShell 5.1 under 'Stop' turns into a fatal error - run it with 'Continue'
    $ErrorActionPreference = 'Continue'
    foreach ($p in $procs) {
        if (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue) {
            & taskkill /PID $p.ProcessId /T /F 2>&1 | Out-Null
        }
    }
    if ($procs) { Write-Host 'Da tat app dang chay.'; Start-Sleep 2 }
    return [bool]$procs
}
trap {
    Write-Host "`nLOI: $_" -ForegroundColor Red
    Write-Result $false "$_"
    if ($LogFile -and -not $NoRestart) { Start-App }   # from the app: bring it back to show the error
    try { if ($LogFile) { Stop-Transcript | Out-Null } } catch {}
    exit 1
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Can Git (git-scm.com) de cap nhat.' }
}
if (-not (Test-Path (Join-Path $Root '.git'))) { throw 'Thu muc nay khong phai ban git clone: hay git clone https://github.com/dinhtrongddr-dev/Affiliate-Agent.git' }

function Get-GitLocks {
    # index.lock, HEAD.lock, refs\remotes\origin\main.lock, packed-refs.lock, config.lock ...
    Get-ChildItem (Join-Path $Root '.git') -Recurse -Force -File -Filter '*.lock' -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\objects\\' }
}
function Clear-GitLock {
    # A git command that was killed midway (window closed during an update, app stopped, power
    # cut) leaves a *.lock file in .git and every later git command fails with "...lock: File
    # exists". Wait briefly in case a real git is still working, then remove the stale locks.
    for ($i = 0; $i -lt 10; $i++) {
        $locks = @(Get-GitLocks)
        if (-not $locks) { return }
        $old = $locks | Where-Object { ((Get-Date) - $_.LastWriteTime).TotalSeconds -gt 60 }
        if ($old.Count -eq $locks.Count -or -not (Get-Process git -ErrorAction SilentlyContinue)) {
            foreach ($l in $locks) {
                Remove-Item $l.FullName -Force -ErrorAction SilentlyContinue
                if (-not (Test-Path $l.FullName)) {
                    Write-Host ('Da go file khoa git con sot: ' + $l.FullName.Substring($Root.Length + 1)) -ForegroundColor Yellow
                }
            }
            break
        }
        Start-Sleep 2
    }
    $left = @(Get-GitLocks)
    if ($left) { throw ("Git dang ban (con " + $left[0].FullName + "): dong cac cua so git/cap nhat khac roi chay lai.") }
}
function Invoke-Git([string[]]$GitArgs) {
    # PowerShell 5.1 turns git's stderr into a terminating error under 'Stop': capture it plainly
    $ErrorActionPreference = 'Continue'
    $o = (& git @GitArgs 2>&1 | ForEach-Object { "$_" } | Out-String)
    return @{ Out = $o; Code = $LASTEXITCODE }
}
Clear-GitLock

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$f = Invoke-Git @('fetch', '--quiet', 'origin', $branch)
if ($f.Code -ne 0 -and $f.Out -match '\.lock') { Clear-GitLock; $f = Invoke-Git @('fetch', '--quiet', 'origin', $branch) }
if ($f.Code -ne 0) {
    Write-Host $f.Out.Trim() -ForegroundColor Red
    throw 'Khong tai duoc ban moi tu GitHub (xem dong loi phia tren). Kiem tra mang roi chay lai.'
}
$behind = [int](git rev-list --count "HEAD..origin/$branch")
$current = (git log -1 --format='%h %cd' --date=format:'%d/%m %H:%M').Trim()
$script:From = (git log -1 --format='%h').Trim()
if ($behind -eq 0) {
    Write-Host "Da la ban moi nhat ($current)." -ForegroundColor Green
    if ($Check) { exit 0 }
    if (-not $Force) {
        if ($LogFile -and -not $NoRestart) {
            # started by the app's button: the code here can be newer than the running app
            # (pulled without a restart), so restart it to run what is on disk
            Stop-App | Out-Null
            Write-Result $true "Da la ban moi nhat ($current), da khoi dong lai app"
            Start-App
            try { Stop-Transcript | Out-Null } catch {}
            exit 0
        }
        Write-Result $true 'Da la ban moi nhat'; exit 0
    }
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

$wasRunning = Stop-App

# Back up the database before the new version touches it.
$db = Join-Path $Root 'state\affiliate-agent.sqlite3'
if (Test-Path $db) {
    $bk = Join-Path $Root ('backups\affiliate-agent-before-update-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.sqlite3')
    New-Item -ItemType Directory -Force -Path (Split-Path $bk) | Out-Null
    Copy-Item $db $bk -Force   # the app is stopped, so a file copy is consistent
    # keep the 5 newest; an old copy that cannot be deleted (e.g. made by an admin window during
    # setup) is skipped - tidying up must never stop the update itself
    Get-ChildItem (Split-Path $bk) -Filter 'affiliate-agent-before-update-*.sqlite3' |
        Sort-Object Name -Descending | Select-Object -Skip 5 | ForEach-Object {
            try { Remove-Item $_.FullName -Force -ErrorAction Stop }
            catch { Write-Host ('(Bo qua, khong xoa duoc ban sao luu cu ' + $_.TargetObject + ')') -ForegroundColor DarkGray }
        }
    Write-Host "Da sao luu CSDL: $bk"
}

$before = (git rev-parse HEAD).Trim()
Clear-GitLock   # stopping the app above may have interrupted a git call
$m = Invoke-Git @('merge', '--ff-only', "origin/$branch")
if ($m.Code -ne 0 -and $m.Out -match '\.lock') { Clear-GitLock; $m = Invoke-Git @('merge', '--ff-only', "origin/$branch") }
Write-Host $m.Out.Trim()
if ($m.Code -ne 0) {
    $ahead = [int](git rev-list --count "origin/$branch..HEAD")
    if ($ahead -gt 0) {
        throw "May nay co $ahead commit rieng chua co tren GitHub: lich su da khac nhau, can xu ly tay."
    }
    throw 'Cap nhat loi (xem dong loi cua git ngay phia tren). Chay lai lenh cap nhat; neu van loi, gui anh cua so nay.'
}

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
Write-Result $true "Da cap nhat: $current -> $now"
if (-not $NoRestart) {
    Start-App
    Write-Host 'Da mo lai app.'
}
try { if ($LogFile) { Stop-Transcript | Out-Null } } catch {}
