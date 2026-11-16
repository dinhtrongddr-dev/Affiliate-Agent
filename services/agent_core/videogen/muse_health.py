"""Profile health without exposing cookies, proxy passwords or upstream error bodies."""
import ipaddress
import time
from datetime import datetime, timezone

import httpx

from . import muse_profiles


def _stamp(value):
    try:
        number = float(value)
        return number if 0 < number <= 253402300799 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _auth_failure(note):
    note = str(note or "").lower()
    return any(token in note for token in (
        "/api/session http 401", "session authentication failed", "会话认证失败",
        "会话已被", "重新导入 cookie", "re-import the cookies"))


def _bound(profile, account):
    return bool(account and profile.get("pushed_at") and
                account.get("id") == profile.get("account_id") and
                account.get("profile_id") == profile["profile_id"] and
                account.get("profile_revision") == profile.get("revision") and
                account.get("proxy_server") == profile["proxy"]["server"])


def describe(profile, account=None):
    """Cookie timestamps describe expiry, never prove an authenticated session."""
    bound = _bound(profile, account)
    account = account if bound else {}
    expiry = _stamp((account.get("cookies_exp") or profile.get("expires") or {}).get("hatch_sess"))
    seconds = expiry - time.time() if expiry else None
    checks = profile.get("health_checks") or {}
    session = checks.get("session") or {}
    proxy = max([account.get("proxy_status") or {}, checks.get("proxy") or {}],
                key=lambda item: _stamp(item.get("checked_at")) or 0)
    observations = [session, proxy]
    # Proxy checks also authenticate Muse. Use the newest session observation.
    if proxy.get("session_ok") is not None and (_stamp(proxy.get("checked_at")) or 0) >= (_stamp(session.get("checked_at")) or 0):
        session = proxy
    remote_at = _stamp(account.get("checked_at")) or 0
    if remote_at >= (_stamp(session.get("checked_at")) or 0) and remote_at:
        session = {"session_ok": account.get("ok"), "checked_at": remote_at,
                   "failure_type": "auth" if _auth_failure(account.get("note")) else None}
        uncertain = any(token in str(account.get("note") or "").lower()
                        for token in ("未确认", "not confirmed", "unconfirmed"))
        if uncertain or (account.get("ok") is not True and not session["failure_type"]):
            session["session_ok"] = None
        observations.append(session)
    definite = [item for item in observations if item.get("session_ok") is True or
                (item.get("session_ok") is False and item.get("failure_type") == "auth")]
    last_definite = max(definite, key=lambda item: _stamp(item.get("checked_at")) or 0, default={})
    if seconds is not None and seconds <= 0:
        cookie_state, cookie_label, tone = "expired", "Cookie đã hết hạn", "err"
    elif last_definite.get("session_ok") is False:
        cookie_state, cookie_label, tone = "invalid", "Cookie die · cần cập nhật", "err"
    elif seconds is not None:
        cookie_state = "expiring" if seconds <= 86400 else "valid"
        cookie_label = "Cookie sắp hết hạn" if seconds <= 86400 else "Cookie còn hạn"
        tone = "warn" if seconds <= 86400 else "info"
    else:
        cookie_state, cookie_label, tone = "unknown", "Cookie chưa rõ hạn", "warn"
    if not bound:
        session_state, session_label = "unknown", "Chưa xác nhận trên gateway"
    elif cookie_state in {"expired", "invalid"}:
        session_state, session_label = "error", "Cần cookie mới"
    elif session.get("session_ok") is True:
        session_state, session_label = "ok", "Đăng nhập Muse OK"
    elif session.get("checked_at"):
        session_state, session_label = "unknown", "Chưa xác nhận · thử lại"
    else:
        session_state, session_label = "unknown", "Chưa test phiên Muse"
    proxy_state = "unknown"
    if bound and proxy.get("checked_at"):
        proxy_state = "ok" if proxy.get("proxy_reachable") is True else "error"
    remaining = None
    if seconds is not None and seconds > 0:
        minutes = max(1, int(seconds // 60))
        remaining = f"Còn {minutes // 1440} ngày {(minutes % 1440) // 60} giờ" if minutes >= 1440 else f"Còn {minutes // 60} giờ {minutes % 60} phút"
    return {"cookie_state": cookie_state, "cookie_label": cookie_label, "cookie_tone": tone,
            "expires_at": datetime.fromtimestamp(expiry, timezone.utc).isoformat() if expiry else None,
            "remaining": remaining, "session_state": session_state, "session_label": session_label,
            "session_checked_at": session.get("checked_at"), "proxy_state": proxy_state,
            "proxy_label": {"ok": "Proxy OK", "error": "Proxy lỗi", "unknown": "Chưa test proxy"}[proxy_state],
            "proxy_checked_at": proxy.get("checked_at") if bound else None,
            "exit_ip": _exit_ip(proxy.get("exit_ip")) if bound else None,
            "can_test": bound}


def _exit_ip(value):
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def check(settings, profile_id, kind, transport=None):
    """One explicit probe through the saved gateway route; never generate content."""
    if kind not in {"session", "proxy"}:
        raise muse_profiles.ProfileError("Loại kiểm tra không hợp lệ")
    with muse_profiles._LOCK:
        profile = next((p for p in muse_profiles.load(settings) if p["profile_id"] == profile_id), None)
    if profile is None:
        raise muse_profiles.ProfileError("Không tìm thấy profile Muse")
    if not profile.get("pushed_at") or not profile.get("account_id"):
        raise muse_profiles.ProfileError("Lưu và đồng bộ profile trước khi kiểm tra")
    key = settings.videogen_api_key.get_secret_value()
    if not settings.videogen_base_url or not key:
        raise muse_profiles.ProfileError("Cấu hình gateway Muse và API key trước khi kiểm tra")
    base = settings.videogen_base_url.rstrip("/")
    try:
        with httpx.Client(timeout=180, transport=transport) as client:
            headers = {"Authorization": "Bearer " + key}
            response = client.get(base + "/admin/accounts", headers=headers)
            if response.status_code != 200:
                raise muse_profiles.ProfileError("Không đọc được trạng thái gateway; kiểm tra kết nối và API key")
            accounts = response.json()["accounts"]
            account = next((a for a in accounts if a.get("id") == profile["account_id"]), None)
            if account:
                account = account | {"proxy_server": (account.get("proxy") or {}).get("server")}
            if not _bound(profile, account):
                raise muse_profiles.ProfileError("Profile trên gateway đã thay đổi; đồng bộ lại trước khi test")
            suffix = "proxy/test" if kind == "proxy" else "test"
            response = client.post(f"{base}/admin/accounts/{profile['account_id']}/{suffix}", headers=headers)
            if response.status_code != 200:
                message = "Profile đang bận; thử lại sau khi tác vụ hoàn tất" if response.status_code == 409 else "Gateway chưa kiểm tra được; kiểm tra kết nối và API key"
                raise muse_profiles.ProfileError(message)
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("ok"), bool):
                raise ValueError
    except muse_profiles.ProfileError:
        raise
    except httpx.HTTPError:
        raise muse_profiles.ProfileError("Gateway chưa trả kết quả; chưa kết luận cookie hay proxy bị lỗi. Hãy kiểm tra trạng thái trước khi thử lại") from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise muse_profiles.ProfileError("Gateway trả kết quả kiểm tra không hợp lệ") from None
    failure = body.get("failure_type")
    auth_failed = failure == "auth" or (body.get("ok") is False and _auth_failure(body.get("message")))
    result = {"checked_at": time.time(), "ok": body["ok"],
              "session_ok": True if body["ok"] else False if auth_failed else None,
              "failure_type": "auth" if auth_failed else failure if failure in {"proxy", "network"} else None}
    if kind == "proxy":
        result.update(proxy_reachable=body.get("proxy_reachable") is True,
                      exit_ip=_exit_ip(body.get("exit_ip")))
    result["message"] = ("Đăng nhập Muse OK" if result["session_ok"] is True else
                         "Cookie die: Muse từ chối đăng nhập; hãy cập nhật cookie mới" if auth_failed else
                         "Chưa xác nhận phiên Muse; kiểm tra proxy và thử lại")
    if kind == "proxy":
        result["message"] = ("Proxy OK · " + result["message"] if result["proxy_reachable"] else
                             "Proxy lỗi hoặc chưa kết nối được; chưa kết luận cookie die")
    with muse_profiles._LOCK:
        saved = muse_profiles.load(settings)
        current = next((p for p in saved if p["profile_id"] == profile_id), None)
        if current is None or current.get("revision") != profile.get("revision"):
            raise muse_profiles.ProfileError("Cookie vừa được cập nhật; bỏ kết quả test phiên cũ và test lại")
        current.setdefault("health_checks", {})[kind] = result
        if kind == "session" and failure == "proxy":
            current["health_checks"]["proxy"] = result | {"proxy_reachable": False}
        muse_profiles.secret_store.save(settings, muse_profiles.NAME, {"profiles": saved})
    return result
