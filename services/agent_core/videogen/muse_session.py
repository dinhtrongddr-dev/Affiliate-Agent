"""Muse login by cookie JSON (the user's own session, exported from their own browser).

Accepted input:
- a browser cookie-export array (Cookie-Editor / EditThisCookie style:
  [{"domain": ".muse.ai", "name": "...", "value": "...", "expirationDate": 1797059748.6}, ...])
- the muse2api import shape: {"cookies": {"name": "value"}, "expires": {"name": 1797059748}}

Only muse.ai cookies are kept; `hatch_sess` is required (the muse2api docs list it as the only
essential cookie). The session is stored DPAPI-encrypted on this machine and can be pushed to the
user's own muse2api gateway (POST /admin/accounts, same payload as the gateway's extension).
Cookie values are never logged or shown back."""
import json
import time
from datetime import datetime, timezone

import httpx

from .. import secret_store

NAME = "muse_session"
ESSENTIAL = ("hatch_sess",)
USEFUL = ("hatch_sess", "hatch_vml", "hatch_gw", "datr")


class SessionError(ValueError):
    pass


def _muse_domain(domain: str) -> bool:
    d = str(domain or "").lstrip(".").lower()
    return d == "muse.ai" or d.endswith(".muse.ai")


def parse(text: str) -> dict:
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise SessionError("Không đọc được JSON (dán nguyên nội dung file export)") from exc
    cookies, expires = {}, {}
    if isinstance(data, dict) and isinstance(data.get("cookies"), dict):
        for k, v in data["cookies"].items():
            cookies[str(k)] = str(v)
        for k, v in (data.get("expires") or {}).items():
            if isinstance(v, (int, float)):
                expires[str(k)] = int(v)
    elif isinstance(data, list):
        for c in data:
            if not isinstance(c, dict) or not _muse_domain(c.get("domain", "")):
                continue
            name, value = str(c.get("name") or ""), c.get("value")
            if not name or value is None:
                continue
            cookies[name] = str(value)
            exp = c.get("expirationDate") or c.get("expires")
            if isinstance(exp, (int, float)):
                expires[name] = int(exp)
    else:
        raise SessionError("Định dạng không hỗ trợ: cần mảng cookie hoặc {cookies, expires}")
    if not cookies:
        raise SessionError("Không có cookie nào của muse.ai")
    missing = [n for n in ESSENTIAL if n not in cookies]
    if missing:
        raise SessionError(f"Thiếu cookie đăng nhập: {', '.join(missing)}")
    now = time.time()
    if expires.get("hatch_sess") and expires["hatch_sess"] < now:
        raise SessionError("Phiên hatch_sess đã hết hạn: đăng nhập lại muse.ai rồi export lại")
    return {"cookies": cookies, "expires": expires}


def summary(session: dict | None) -> dict | None:
    """Safe description (no values) for the UI."""
    if not session:
        return None

    def date(ts):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%d/%m/%Y") if ts else None

    exp = session.get("expires") or {}
    days = None
    if exp.get("hatch_sess"):
        days = int((exp["hatch_sess"] - time.time()) // 86400)
    return {
        "names": sorted(session["cookies"]),
        "useful": [n for n in USEFUL if n in session["cookies"]],
        "session_expires": date(exp.get("hatch_sess")),
        "days_left": days,
        "label": session.get("label") or "",
        "imported_at": session.get("imported_at"),
        "pushed_at": session.get("pushed_at"),
        "push_result": session.get("push_result"),
    }


def save(settings, text: str, label: str = "") -> dict:
    session = parse(text)
    session["label"] = (label or "muse-main")[:60]
    session["imported_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    secret_store.save(settings, NAME, session)
    return summary(session)


def load(settings) -> dict | None:
    return secret_store.load(settings, NAME)


def delete(settings) -> bool:
    return secret_store.delete(settings, NAME)


def push_to_gateway(settings, transport=None) -> dict:
    """Send the stored session to the user's muse2api gateway as one account."""
    session = load(settings)
    if not session:
        raise SessionError("Chưa nhập phiên Muse")
    if (settings.videogen_provider or "").lower() != "muse2api" or not settings.videogen_base_url:
        raise SessionError("Chưa cấu hình muse2api (Cài đặt → Nguồn hình)")
    key = settings.videogen_api_key.get_secret_value()
    if not key:
        raise SessionError("Thiếu API key (MUSE2API_KEY) của gateway")
    base = settings.videogen_base_url.rstrip("/")
    with httpx.Client(timeout=60, transport=transport) as client:
        resp = client.post(f"{base}/admin/accounts",
                           headers={"Authorization": f"Bearer {key}"},
                           json={"label": session.get("label") or "muse-main",
                                 "cookies": session["cookies"], "expires": session["expires"]})
    try:
        body = resp.json()
    except ValueError:
        body = {"text": resp.text[:300]}
    if resp.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else body
        raise SessionError(f"Gateway từ chối: HTTP {resp.status_code} {str(detail)[:300]}")
    session["pushed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Keep only non-secret fields of the gateway's answer.
    safe = {k: v for k, v in (body.items() if isinstance(body, dict) else [])
            if k in ("id", "label", "status", "ok", "imported", "updated", "warnings", "message")}
    session["push_result"] = safe
    secret_store.save(settings, NAME, session)
    return safe
