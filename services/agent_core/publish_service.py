from pathlib import Path

from .media import probe_video
from .meta_reels import MetaReelsPublisher
from .quality import validate_facebook_reel


class PublishDisabled(RuntimeError):
    pass


def publish_facebook_reel(settings, video_path: Path, description: str, title: str = "") -> dict:
    if not settings.auto_publish:
        raise PublishDisabled("AA_AUTO_PUBLISH is false; Facebook publication is disabled.")
    if not settings.meta_enabled:
        raise PublishDisabled("AA_META_ENABLED is false; connect the Facebook Page first.")
    token = settings.meta_page_access_token.get_secret_value()
    if not token:
        raise PublishDisabled("Facebook Page Access Token is missing.")

    metadata = probe_video(Path(video_path), settings.ffprobe_path)
    qa = validate_facebook_reel(metadata, settings)
    if not qa.passed:
        raise ValueError("Facebook Reel QA failed: " + "; ".join(qa.reasons))

    publisher = MetaReelsPublisher(
        graph_version=settings.meta_graph_version,
        page_id=settings.meta_page_id,
        page_access_token=token,
    )
    return publisher.publish_reel(Path(video_path), description=description, title=title)
