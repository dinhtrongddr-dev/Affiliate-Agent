# AffiliateAgent - free disk space without touching data you need.
#
#   powershell -ExecutionPolicy Bypass -File scripts\clean.ps1              # job files older than 14 days
#   powershell -ExecutionPolicy Bypass -File scripts\clean.ps1 -Days 3
#   powershell -ExecutionPolicy Bypass -File scripts\clean.ps1 -WhatIf      # only show what would go
#
# Removes: per-job work folders (Muse clips, render temp) of jobs not touched for -Days days,
# pip/test caches, __pycache__, rotated logs. Keeps: the database, finished videos in the
# media folder, the TTS cache of the last -Days days, settings, keys.
param([int]$Days = 14, [switch]$WhatIf)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$cut = (Get-Date).AddDays(-$Days)
$targets = [System.Collections.Generic.List[string]]::new()

$work = Join-Path $Root 'cache\work'
if (Test-Path $work) {
    Get-ChildItem $work -Directory | Where-Object { $_.Name -ne '_tts_cache' -and $_.LastWriteTime -lt $cut } |
        ForEach-Object { $targets.Add($_.FullName) }
    $tts = Join-Path $work '_tts_cache'
    if (Test-Path $tts) {
        Get-ChildItem $tts -File | Where-Object { $_.LastWriteTime -lt $cut } | ForEach-Object { $targets.Add($_.FullName) }
    }
}
foreach ($d in @('cache\pip', 'cache\uitest', 'temp', '.pytest_cache', '.ruff_cache')) {
    $p = Join-Path $Root $d
    if (Test-Path $p) { $targets.Add($p) }
}
Get-ChildItem $Root -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\.venv\\' } | ForEach-Object { $targets.Add($_.FullName) }
Get-ChildItem (Join-Path $Root 'logs') -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '\.log\.\d+$' } | ForEach-Object { $targets.Add($_.FullName) }

$total = 0
foreach ($t in $targets) {
    $size = (Get-ChildItem $t -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    if (-not $size -and (Test-Path $t -PathType Leaf)) { $size = (Get-Item $t).Length }
    $total += [int64]$size
    if ($WhatIf) { Write-Host ("  se xoa {0,8:N1} MB  {1}" -f ($size / 1MB), $t) }
    else { Remove-Item $t -Recurse -Force -ErrorAction SilentlyContinue }
}
$verb = if ($WhatIf) { 'Se giai phong' } else { 'Da giai phong' }
Write-Host ("{0} {1:N2} GB ({2} muc)." -f $verb, ($total / 1GB), $targets.Count) -ForegroundColor Green
