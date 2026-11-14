"""Setup check: start the bundled Muse gateway once, report, stop it. Exit 0 = OK.

A gateway the app already runs (on the same port) counts as OK and is left alone.
"""
import sys

from agent_core.settings import get_settings
from agent_core.videogen import gateway


def main() -> int:
    settings = get_settings()
    if not gateway.enabled(settings):
        print("Muse gateway: tat (AA_VIDEOGEN_PROVIDER khac muse2api hoac AA_MUSE_EMBEDDED=false)")
        return 0
    if gateway.healthy(settings):
        print(f"Muse gateway: dang chay tai {gateway.base_url(settings)}")
        return 0
    proc = gateway.GatewayProcess(settings)
    try:
        if proc.start(wait=60):
            print(f"Muse gateway: OK ({gateway.base_url(settings)})")
            return 0
        print(f"Muse gateway LOI: {proc.error or 'khong khoi dong duoc'} - xem logs/muse-gateway.log")
        return 1
    finally:
        proc.stop()


if __name__ == "__main__":
    sys.exit(main())
