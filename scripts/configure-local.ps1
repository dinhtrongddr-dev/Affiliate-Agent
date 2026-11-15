$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $Root '.env'
$ExamplePath = Join-Path $Root 'config\.env.example'
# ChatGPT2API folder: -ChatGPT2ApiDir, else next to this repo, else D:\chatgpt2api-web
$ChatGPT2ApiDir = if ($args.Count -gt 0) { $args[0] } else {
    @((Join-Path (Split-Path -Parent $Root) 'chatgpt2api-web'), 'D:\chatgpt2api-web', 'C:\chatgpt2api-web') |
        Where-Object { Test-Path (Join-Path $_ '.env') } | Select-Object -First 1
}
$ChatGPT2ApiEnv = if ($ChatGPT2ApiDir) { Join-Path $ChatGPT2ApiDir '.env' } else { 'chatgpt2api-web\.env' }
$CredentialPath = Join-Path $Root 'config\shopee-skill\credentials.json'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

if (-not (Test-Path $EnvPath)) {
    Copy-Item $ExamplePath $EnvPath
}
if (-not (Test-Path $ChatGPT2ApiEnv)) {
    throw "ChatGPT2API .env not found: $ChatGPT2ApiEnv"
}

$script:EnvLines = [System.Collections.Generic.List[string]]::new()
[System.IO.File]::ReadAllLines($EnvPath, [System.Text.Encoding]::UTF8) |
    ForEach-Object { $script:EnvLines.Add($_) }

function Set-EnvLine {
    param([string]$Key, [string]$Value)

    $Pattern = '^\s*' + [regex]::Escape($Key) + '\s*='
    for ($Index = 0; $Index -lt $script:EnvLines.Count; $Index++) {
        if ($script:EnvLines[$Index] -match $Pattern) {
            $script:EnvLines[$Index] = "$Key=$Value"
            return
        }
    }
    $script:EnvLines.Add("$Key=$Value")
}

function Convert-SecureStringToPlainText {
    param([Security.SecureString]$Value)

    if ($null -eq $Value -or $Value.Length -eq 0) { return '' }
    $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    }
}

# Reuse the existing local ChatGPT2API key; never print its value.
$KeyLine = Get-Content $ChatGPT2ApiEnv -Encoding UTF8 |
    Where-Object { $_ -match '^\s*CHATGPT2API_AUTH_KEY\s*=' } |
    Select-Object -First 1
if (-not $KeyLine) {
    throw 'CHATGPT2API_AUTH_KEY was not found in ChatGPT2API .env.'
}
$AIKey = ($KeyLine -split '=', 2)[1].Trim()
if ($AIKey.Length -ge 2 -and
    (($AIKey.StartsWith('"') -and $AIKey.EndsWith('"')) -or
     ($AIKey.StartsWith("'") -and $AIKey.EndsWith("'")))) {
    $AIKey = $AIKey.Substring(1, $AIKey.Length - 2)
}
if ([string]::IsNullOrWhiteSpace($AIKey)) {
    throw 'CHATGPT2API_AUTH_KEY is empty. Configure it in ChatGPT2API first.'
}

Set-EnvLine 'AA_AI_PROTOCOL' 'openai'
Set-EnvLine 'AA_AI_BASE_URL' 'http://127.0.0.1:13000/v1'
Set-EnvLine 'AA_AI_API_KEY' $AIKey
Set-EnvLine 'AA_AI_MODEL' 'gpt-6-thinking'
Set-EnvLine 'AA_AI_THINKING_EFFORT' ''
Write-Host 'AffiliateAgent now points to local ChatGPT2API (key hidden).'
Write-Host 'Model: gpt-6-thinking | Thinking: default'

$UpdateShopee = Read-Host 'Configure Shopee Affiliate App ID and Secret now? (y/n)'
if ($UpdateShopee -match '^(y|yes)$') {
    $AppId = Read-Host 'Shopee Affiliate App ID'
    $SecureSecret = Read-Host 'Shopee Affiliate App Secret' -AsSecureString
    $AppSecret = Convert-SecureStringToPlainText $SecureSecret

    if (-not [string]::IsNullOrWhiteSpace($AppId) -and
        -not [string]::IsNullOrWhiteSpace($AppSecret)) {

        if (Test-Path $CredentialPath) {
            $Credentials = Get-Content $CredentialPath -Raw -Encoding UTF8 | ConvertFrom-Json
        }
        else {
            $Credentials = [pscustomobject]@{
                shopee_affiliate = [pscustomobject]@{
                    app_id = ''
                    secret = ''
                    base_url = 'https://open-api.affiliate.shopee.vn/graphql'
                }
            }
        }

        if ($null -eq $Credentials.shopee_affiliate) {
            $Credentials | Add-Member -NotePropertyName shopee_affiliate -NotePropertyValue ([pscustomobject]@{})
        }
        $Credentials.shopee_affiliate.app_id = $AppId
        $Credentials.shopee_affiliate.secret = $AppSecret
        if ([string]::IsNullOrWhiteSpace($Credentials.shopee_affiliate.base_url)) {
            $Credentials.shopee_affiliate.base_url = 'https://open-api.affiliate.shopee.vn/graphql'
        }

        $Json = $Credentials | ConvertTo-Json -Depth 12
        [System.IO.File]::WriteAllText($CredentialPath, $Json + [Environment]::NewLine, $Utf8NoBom)
        Set-EnvLine 'AA_SHOPEE_AFFILIATE_ENABLED' 'true'
        Write-Host 'Shopee Affiliate credentials saved locally (values hidden).'
    }
    else {
        Set-EnvLine 'AA_SHOPEE_AFFILIATE_ENABLED' 'false'
        Write-Host 'Shopee credentials were incomplete; Affiliate API remains disabled.'
    }

    $AppSecret = $null
    $SecureSecret = $null
}

[System.IO.File]::WriteAllText(
    $EnvPath,
    (($script:EnvLines -join [Environment]::NewLine) + [Environment]::NewLine),
    $Utf8NoBom
)

$AIKey = $null
Write-Host ''
Write-Host 'Local configuration saved.'
Write-Host 'Verify without revealing secrets using:'
Write-Host '  .\.venv\Scripts\python.exe -m agent_core.cli credentials'
Write-Host 'AUTO_PUBLISH remains false. No publishing was enabled.'
