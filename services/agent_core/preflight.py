"""Quick checks before making content: is everything a video needs actually working?

Each check returns {"name", "ok", "required", "detail"}. "required" checks block new videos
(AI, Muse when AI clips are on, ffmpeg, disk); the others only warn (Facebook, voice).
Results are cached for a few minutes so creating several videos does not repeat the AI call.
"""
import shutil
import time

_cache: dict = {}
TTL = 300


def _check(name, ok, detail="", required=True):
    return {"name": name, "ok": bool(ok), "required": required, "detail": str(detail)[:300]}


def check_ai(settings) -> dict:
    """One tiny real request: catches a logged-out ChatGPT2API, a wrong key or a dead server."""
    try:
        from .workflow import _client
        reply = _client(settings).chat([{"role": "user", "content": "Trả lời đúng một chữ: OK"}],
                                       temperature=0, max_tokens=20)
        text = str(reply.get("text") or reply.get("content") or reply)[:60]
        return _check(f"AI ({settings.ai_model})", True, f"trả lời: {text}")
    except Exception as exc:  # noqa: BLE001
        return _check(f"AI ({settings.ai_model})", False, f"Không gọi được AI: {exc}")


def check_muse(settings) -> list[dict]:
    from .videogen import gateway
    if settings.videogen_provider != "muse2api":
        return [_check("Muse", True, "không dùng Muse", required=False)]
    if not gateway.healthy(settings, timeout=5):
        return [_check("Cổng Muse (muse2api)", False, "Gateway không trả lời; khởi động lại ở trang Clip AI")]
    info = gateway.account_status(settings)
    accounts = info.get("accounts") or []
    usable = [a for a in accounts if a.get("enabled", True) and not a.get("last_error")]
    out = [_check("Cổng Muse (muse2api)", True, "đang chạy"),
           _check("Tài khoản Muse", bool(usable),
                  f"{len(usable)}/{len(accounts)} tài khoản dùng được" if accounts
                  else "Chưa có tài khoản/profile Muse nào")]
    out.append(check_muse_session(settings))
    return out


def check_muse_session(settings) -> dict:
    """A real request to muse.ai: open each enabled profile's session through its own proxy
    (the 'Test phiên Muse' probe). It never generates content, so it costs no Muse quota."""
    from .videogen import muse_health, muse_profiles
    try:
        profiles = [p for p in muse_profiles.load(settings) if p.get("enabled", True)]
    except Exception as exc:  # noqa: BLE001
        return _check("Phiên Muse (muse.ai)", False, f"Không đọc được profile: {exc}")
    if not profiles:
        return _check("Phiên Muse (muse.ai)", True, "dùng phiên cũ (không có profile)", required=False)
    good, notes = 0, []
    for p in profiles:
        try:
            r = muse_health.check(settings, p["profile_id"], "session")
            if r.get("ok"):
                good += 1
            else:
                notes.append(f"{p['profile_id']}: {r.get('message') or 'phiên lỗi'}")
        except Exception as exc:  # noqa: BLE001
            if "bận" in str(exc):     # the account is making a clip right now: its session is live
                good += 1
                notes.append(f"{p['profile_id']}: đang tạo clip (phiên đang hoạt động)")
            else:
                notes.append(f"{p['profile_id']}: {exc}")
    detail = f"{good}/{len(profiles)} profile mở được phiên muse.ai qua proxy"
    return _check("Phiên Muse (muse.ai)", good > 0, detail + (" · " + "; ".join(notes) if notes else ""))


def check_tools(settings) -> list[dict]:
    out = [_check("FFmpeg", shutil.which(settings.ffmpeg_path) and shutil.which(settings.ffprobe_path),
                  "ffmpeg/ffprobe có trong PATH" if shutil.which(settings.ffmpeg_path) else "Thiếu ffmpeg")]
    try:
        free = shutil.disk_usage(settings.media_root).free / 1024 ** 3
        out.append(_check("Dung lượng ổ media", free >= 3, f"còn {free:.1f} GB (cần ≥ 3 GB; dọn rác ở trang Video)"))
    except OSError as exc:
        out.append(_check("Dung lượng ổ media", False, f"Không đọc được {settings.media_root}: {exc}"))
    return out


def check_facebook(settings) -> dict:
    if not settings.meta_enabled:
        return _check("Facebook Page", False, "Chưa bật (chỉ cần khi đăng)", required=False)
    try:
        from .meta_reels import MetaReelsPublisher
        page = MetaReelsPublisher(settings.meta_graph_version, settings.meta_page_id,
                                  settings.meta_page_access_token.get_secret_value()).check_page()
        return _check("Facebook Page", True, f"Page: {page.get('name')}", required=False)
    except Exception as exc:  # noqa: BLE001
        return _check("Facebook Page", False, f"Token/Page lỗi: {exc}", required=False)


def run(settings, fresh: bool = False, facebook: bool = False) -> dict:
    key = (str(settings.database_path), facebook)
    hit = _cache.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < TTL:
        return hit[1]
    checks = [check_ai(settings), *check_muse(settings), *check_tools(settings)]
    if facebook:
        checks.append(check_facebook(settings))
    blocking = [c for c in checks if c["required"] and not c["ok"]]
    result = {"ok": not blocking, "checks": checks,
              "blocking": [f"{c['name']}: {c['detail']}" for c in blocking], "at": time.time()}
    if result["ok"]:
        _cache[key] = (time.monotonic(), result)   # only a pass is reused; a failure re-checks next time
    return result
