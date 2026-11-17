"""Test-wide defaults: no live preflight calls (AI, Muse) when tests create jobs."""
import os

os.environ.setdefault("AA_PREFLIGHT_ENABLED", "false")
