# AffiliateAgent - full install on a new Windows machine, one line in PowerShell:
#
#   irm https://raw.githubusercontent.com/dinhtrongddr-dev/Affiliate-Agent/main/scripts/install.ps1 | iex
#
# or, with options, after downloading this file:
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Dir E:\AffiliateSuite -Import D:\affiliate-export.zip
#
# Without -Dir it asks where to install (lists the drives and their free space; Enter = suggested).
# If an earlier install exists it asks: Enter = update it, 2 = pick another drive/folder.
#
# Installs Git if needed, clones (or updates) Affiliate-Agent and chatgpt2api-web side by side,
# then runs scripts\setup.ps1, which installs Python, ffmpeg, libraries, the voice model,
# sets up and starts ChatGPT2API, wires the key and creates Desktop shortcuts.
param(
    [string]$Dir = '',
    [string]$Import = ''
)
$ErrorActionPreference = 'Stop'
$AppRepo = 'https://github.com/dinhtrongddr-dev/Affiliate-Agent.git'
$C2ARepo = 'https://github.com/dinhtrongddr-dev/chatgpt2api-web.git'

function Choose-Dir {
    # Lists the fixed drives with free space; Enter = suggested folder, a number = that drive,
    # or a full folder path. The suggestion is the non-system drive with the most free space.
    $drives = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction SilentlyContinue |
                Sort-Object DeviceID)
    $best = $drives | Where-Object { $_.DeviceID -ne $env:SystemDrive } | Sort-Object FreeSpace -Descending |
            Select-Object -First 1
    if (-not $best) { $best = $drives | Select-Object -First 1 }
    $default = if ($best) { "$($best.DeviceID)\AffiliateSuite" } else { Join-Path $env:USERPROFILE 'AffiliateSuite' }
    Write-Host "`nChon noi cai dat (can khoang 6-8 GB trong):" -ForegroundColor Cyan
    for ($i = 0; $i -lt $drives.Count; $i++) {
        $d = $drives[$i]
        $free = [math]::Round($d.FreeSpace / 1GB, 1)
        $warn = if ($free -lt 8) { '  (it cho)' } else { '' }
        Write-Host ("  [{0}] O {1}  trong {2} GB / {3} GB{4}" -f ($i + 1), $d.DeviceID, $free,
                    [math]::Round($d.Size / 1GB, 0), $warn)
    }
    Write-Host '  Hoac go duong dan day du, vi du: E:\Apps\AffiliateSuite'
    while ($true) {
        $ans = "$(Read-Host "Go so o dia / duong dan, hoac Enter de cai vao $default")".Trim().Trim('"')
        if (-not $ans) { return $default }
        if ($ans -match '^\d+$' -and [int]$ans -ge 1 -and [int]$ans -le $drives.Count) {
            return "$($drives[[int]$ans - 1].DeviceID)\AffiliateSuite"
        }
        if ($ans -match '^[A-Za-z]:\\' -and (Test-Path $ans.Substring(0, 3))) { return $ans.TrimEnd('\') }
        Write-Host 'Khong hop le: go so o dia (vd 2), duong dan day du (vd E:\AffiliateSuite) hoac Enter.' -ForegroundColor Yellow
    }
}
if (-not $Dir) {
    # an earlier install is updated in place instead of asking again
    $existing = @('C:', 'D:', 'E:', 'F:', 'G:') | ForEach-Object { "$_\AffiliateSuite" }
    $existing += Join-Path $env:USERPROFILE 'AffiliateSuite'
    $found = $existing | Where-Object { Test-Path (Join-Path $_ 'Affiliate-Agent\.git') } | Select-Object -First 1
    if ($found) {
        Write-Host "`nDa co ban cai tai $found" -ForegroundColor Cyan
        $ans = "$(Read-Host 'Enter = cap nhat ban nay; go 2 = chon o dia / thu muc khac')".Trim()
        $Dir = if ($ans -eq '2') { Choose-Dir } else { $found }
    } else {
        $Dir = Choose-Dir
    }
}
Write-Host "Cai dat vao: $Dir" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $Dir | Out-Null

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'May chua co winget (App Installer). Cai Git tu https://git-scm.com roi chay lai.'
    }
    Write-Host 'Dang cai Git...' -ForegroundColor Yellow
    winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
    Refresh-Path
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Da cai Git nhung chua thay: mo PowerShell moi roi chay lai.' }
}

function Get-Repo($url, $path) {
    if (Test-Path (Join-Path $path '.git')) {
        Write-Host "Da co $path : cap nhat..."
        git -C $path pull --ff-only --quiet
    } elseif (Test-Path $path) {
        throw "$path da ton tai nhung khong phai ban git: doi ten/xoa thu muc do hoac dung -Dir khac."
    } else {
        Write-Host "Dang tai $url ..."
        git clone --quiet $url $path
    }
}
$app = Join-Path $Dir 'Affiliate-Agent'
$c2a = Join-Path $Dir 'chatgpt2api-web'
Get-Repo $AppRepo $app
Get-Repo $C2ARepo $c2a

$args2 = @('-ExecutionPolicy', 'Bypass', '-File', (Join-Path $app 'scripts\setup.ps1'), '-ChatGPT2ApiDir', $c2a)
if ($Import) { $args2 += @('-Import', $Import) }
& powershell @args2
if ($LASTEXITCODE -ne 0) { throw 'setup.ps1 bao loi: xem thong bao o tren, sua roi chay lai lenh cai dat (an toan khi chay lai).' }
Write-Host "`nHoan tat. Mo app bang loi tat 'Affiliate Agent' tren Desktop." -ForegroundColor Green
