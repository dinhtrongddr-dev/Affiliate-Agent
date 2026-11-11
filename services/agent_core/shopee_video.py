import re
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Literal

from playwright.sync_api import sync_playwright

from .media import probe_video


class ShopeeVideoError(RuntimeError):
    pass


SUCCESS_TEXT = re.compile(r"thành công|đã lưu|đã đăng|successfully|saved", re.IGNORECASE)
BUTTON_NAMES = {
    "draft": re.compile(r"^\s*lưu bản nháp\s*$|^\s*save (as )?draft\s*$", re.IGNORECASE),
    "publish": re.compile(r"^\s*đăng( ngay)?\s*$|^\s*post\s*$|^\s*publish\s*$", re.IGNORECASE),
}


def submit_and_verify(page, action: Literal["draft", "publish"], timeout_ms: int = 60000) -> dict:
    """Click the submit button and look for a real success signal.

    Status is *_CONFIRMED only when Shopee shows a success toast or moves to the
    post list; otherwise it stays *_UNCONFIRMED and must be reconciled by hand.
    """
    confirmed_status = "DRAFT_SAVED_CONFIRMED" if action == "draft" else "PUBLISHED_CONFIRMED"
    unconfirmed_status = (
        "DRAFT_SUBMITTED_UNCONFIRMED" if action == "draft" else "SUBMITTED_UNCONFIRMED"
    )
    start_url = page.url
    button = page.get_by_role("button", name=BUTTON_NAMES[action]).first
    try:
        button.wait_for(state="visible", timeout=15000)
        button.click(timeout=15000)
    except Exception as exc:
        return {"status": unconfirmed_status, "url": page.url,
                "reason": f"Submit button not clickable: {exc}"}

    deadline_steps = max(1, timeout_ms // 1000)
    for _ in range(deadline_steps):
        try:
            if page.get_by_text(SUCCESS_TEXT).first.is_visible():
                return {"status": confirmed_status, "url": page.url, "signal": "success_text"}
        except Exception:
            pass
        url = page.url
        if url != start_url and "/video-upload/upload" not in url and (
            "video-list" in url or "/creator-center/video" in url or "manage" in url
        ):
            return {"status": confirmed_status, "url": url, "signal": "redirect_to_list"}
        page.wait_for_timeout(1000)
    return {
        "status": unconfirmed_status,
        "url": page.url,
        "url_changed": page.url != start_url,
        "reason": "No Shopee success signal seen; verify in Creator Center before retrying.",
    }


def load_upstream_uploader(settings):
    source = (
        settings.project_root
        / "vendor"
        / "luno-automation"
        / "backend"
        / "shopee_uploader.py"
    )
    if not source.is_file():
        raise FileNotFoundError(source)

    module_name = "affiliate_agent_luno_shopee_uploader"
    if module_name in sys.modules:
        module = sys.modules[module_name]
    else:
        spec = spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise ShopeeVideoError(f"Cannot load upstream uploader: {source}")
        module = module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

    module.CONFIG.update({
        "headless": False,
        "profile_dir": settings.project_root / "profiles" / "shopee",
        "log_csv": settings.project_root / "logs" / "shopee_upload_log.csv",
        "state_json": settings.project_root / "state" / "shopee_upload_state.json",
    })
    return module


def check_shopee_integration(settings) -> dict:
    source = (
        settings.project_root
        / "vendor"
        / "luno-automation"
        / "backend"
        / "shopee_uploader.py"
    )
    affiliate = (
        settings.project_root
        / "vendor"
        / "shopee-skill"
        / "scripts"
        / "shopee_affiliate.py"
    )
    module = load_upstream_uploader(settings)
    required = (
        "ensure_login",
        "upload_video_file",
        "set_caption",
        "add_hashtags",
        "set_cover",
        "add_product_link",
        "set_schedule",
        "publish",
        "save_draft",
    )
    missing = [name for name in required if not callable(getattr(module, name, None))]
    return {
        "luno_uploader": source.is_file(),
        "shopee_affiliate_api": affiliate.is_file(),
        "playwright_import": True,
        "required_functions": list(required),
        "missing_functions": missing,
        "ready": source.is_file() and affiliate.is_file() and not missing,
    }


def upload_shopee_video(
    settings,
    video_path: Path,
    caption: str,
    hashtags: list[str] | None = None,
    cover_ratio: float = 0.1,
    product_query: str = "",
    product_id: str | None = None,
    schedule_date: str | None = None,
    schedule_time: str | None = None,
    action: Literal["draft", "publish"] = "draft",
    confirmed: bool = False,
) -> dict:
    """Use the minimal upstream Shopee uploader; never auto-publish by default."""
    if action not in {"draft", "publish"}:
        raise ValueError("action must be 'draft' or 'publish'")
    if action == "publish":
        if not confirmed:
            raise ShopeeVideoError("Live publishing requires explicit confirmation.")
        if not settings.auto_publish:
            raise ShopeeVideoError("AA_AUTO_PUBLISH is false; live publishing is disabled.")
    if not caption.strip():
        raise ValueError("A caption is required")
    if not 0 <= cover_ratio <= 1:
        raise ValueError("cover_ratio must be between 0 and 1")
    if bool(schedule_date) != bool(schedule_time):
        raise ValueError("schedule_date and schedule_time must be supplied together")

    video_path = Path(video_path).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(video_path)
    metadata = probe_video(video_path, settings.ffprobe_path)
    uploader = load_upstream_uploader(settings)

    with sync_playwright() as playwright:
        _browser, context = uploader.ensure_login(playwright)
        try:
            page = context.new_page()
            page.goto(
                uploader.CONFIG["upload_url"],
                wait_until="domcontentloaded",
                timeout=uploader.CONFIG["timeout"],
            )
            if not uploader.check_login(page):
                raise ShopeeVideoError(
                    "Shopee Creator Center is not authenticated in the dedicated Chrome profile."
                )

            uploader.upload_video_file(page, video_path)
            uploader.set_caption(page, caption)
            uploader.add_hashtags(page, hashtags or [])
            uploader.set_cover(page, cover_ratio)
            if product_query:
                uploader.add_product_link(page, product_query, product_id)
            elif product_id:
                raise ValueError("product_id cannot be used without product_query")
            if schedule_date and schedule_time:
                uploader.set_schedule(page, schedule_date, schedule_time)

            result = submit_and_verify(page, action)
            return {
                "status": result.get(
                    "status",
                    "DRAFT_SUBMITTED_UNCONFIRMED"
                    if action == "draft"
                    else "SUBMITTED_UNCONFIRMED",
                ),
                "video_path": str(video_path),
                "metadata": metadata,
                "product_query": product_query,
                "product_id": product_id,
                "result": result,
            }
        finally:
            context.close()
