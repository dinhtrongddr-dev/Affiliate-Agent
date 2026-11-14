"""setup.ps1 helper: one real AI round trip (key, gateway and a logged-in ChatGPT account)."""
import sys

from agent_core import workflow
from agent_core.settings import get_settings

try:
    settings = get_settings()
    reply = workflow._ask_json(workflow._client(settings), settings,
                               'Trả về JSON {"ok": true}', max_tokens=40)
    sys.exit(0 if reply.get("ok") else 1)
except Exception as exc:  # noqa: BLE001
    print(f"AI chua san sang: {str(exc)[:160]}")
    sys.exit(1)
