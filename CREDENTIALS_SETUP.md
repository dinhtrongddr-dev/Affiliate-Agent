# Local credentials and AI gateway setup

## AI gateway: ChatGPT2API

AffiliateAgent uses the existing local ChatGPT2API service directly. It does not use 9Router.

- Base URL: http://127.0.0.1:13000/v1
- Protocol: OpenAI Chat Completions
- Model: gpt-6-astra-wm
- Thinking effort: extended

Run the following from a local PowerShell window:

    cd D:\AffiliateAgent
    .\scripts\configure-local.ps1

The helper reads CHATGPT2API_AUTH_KEY from D:\chatgpt2api-web\.env and copies it into AffiliateAgent's local .env without printing the value. It configures the endpoint, model and thinking effort. The key stays on this Windows machine.

Verify the configuration:

    $env:PYTHONPATH = 'D:\AffiliateAgent\services'
    .\.venv\Scripts\python.exe -m agent_core.cli credentials
    .\.venv\Scripts\python.exe -m agent_core.cli doctor

The AI gateway should report protocol=openai, model=gpt-6-astra-wm and configured=true.

ChatGPT2API's current Web conduit normalizes thinking_effort=extended to its supported deep-reasoning mode. Values such as high are normalized to no additional thinking effort by this installed version.

## Shopee Affiliate

Enter the Shopee Affiliate App ID and App Secret locally through the helper, or store them in:

    D:\AffiliateAgent\config\shopee-skill\credentials.json

Enable the integration only after both values are configured:

    AA_SHOPEE_AFFILIATE_ENABLED=true

Verify with:

    .\.venv\Scripts\python.exe -m agent_core.cli credentials
    .\.venv\Scripts\python.exe -m agent_core.cli shopee-check

## Facebook Page

Meta publishing remains disabled until the Page ID, Page Access Token and Graph API version are configured locally. Never paste Page tokens into ChatGPT.

## Safety defaults

- AA_AUTO_PUBLISH=false
- Shopee uploads require an explicit --confirm and are draft-only.
- Facebook publishing requires AA_META_ENABLED=true, AA_AUTO_PUBLISH=true and an explicit --confirm.
- Keep API keys, App Secrets, Page tokens and browser sessions on the local machine.
