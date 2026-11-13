"""Optional lip-sync for talking AI shots (method B).

Plug point only: with AA_LIPSYNC_PROVIDER=none (default) nothing is sent anywhere and the
render uses re-voicing (voice_track). A provider (sync.so, LatentSync on fal/Replicate) needs
its own account and key from the owner; until then ``apply`` returns None.
"""
import logging
from pathlib import Path

log = logging.getLogger("affiliate_agent.lipsync")

PROVIDERS = {"none"}


def available(settings) -> bool:
    return str(getattr(settings, "lipsync_provider", "none") or "none").lower() in PROVIDERS - {"none"}


def apply(settings, clip: Path, audio: Path, out: Path) -> Path | None:
    """Return a lip-synced copy of ``clip`` driven by ``audio``, or None when not configured."""
    if not available(settings):
        return None
    log.info("lip-sync provider %s not implemented yet", settings.lipsync_provider)
    return None
