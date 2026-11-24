"""Test-wide defaults: no live preflight calls (AI, Muse) when tests create jobs."""
import os

os.environ.setdefault("AA_PREFLIGHT_ENABLED", "false")
# Tests never use the owner's real AI server (.env may point at a VPS).
os.environ["AA_AI_BASE_URL"] = "http://127.0.0.1:13000/v1"
