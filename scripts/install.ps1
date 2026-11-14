# AffiliateAgent - full install on a new Windows machine, one line in PowerShell:
#
#   irm https://raw.githubusercontent.com/dinhtrongddr-dev/Affiliate-Agent/main/scripts/install.ps1 | iex
#
# or, with options, after downloading this file:
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Dir D:\AffiliateSuite -Import D:\affiliate-export.zip
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

if (-not $Dir) {
    $Dir = if (Test-Path 'D:\') { 'D:\AffiliateSuite' } else { Join-Path $env:USERPROFILE 'AffiliateSuite' }
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
