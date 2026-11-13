"""Encrypted Muse profiles: one cookie session and one proxy per profile.

The browser cookie-export format is unchanged. A single export can be paired
with the profile/proxy form, or several profiles can be imported as JSON.
Only non-secret summaries are returned to the web UI.
"""
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

import httpx

from .. import secret_store
from . import muse_session

NAME = "muse_profiles"
MAX_PROFILES = 100
_LOCK = threading.RLock()


class ProfileError(ValueError):
    pass


def _proxy(value) -> dict:
    if isinstance(value, str):
        value = {"server": value}
    if not isinstance(value, dict) or not isinstance(value.get("server"), str):
        raise ProfileError("Mỗi profile cần một proxy HTTP hoặc SOCKS5")
    try:
        raw = value["server"].strip()
        parsed = urlsplit(raw)
        host = parsed.hostname
        port = parsed.port if parsed.port is not None else (1080 if parsed.scheme in {"socks5", "socks5h"} else 80)
        if (parsed.scheme not in {"http", "socks5", "socks5h"} or not host
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or not 1 <= port <= 65535 or any(c.isspace() or ord(c) < 32 for c in raw)
                or any(c in host for c in "/\\@%")):
            raise ValueError
        username = value.get("username", "")
        password = value.get("password", "")
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            username = unquote(parsed.username or "", errors="strict")
            password = unquote(parsed.password or "", errors="strict")
        elif username and "password" not in value:
            raise ProfileError("Nhập mật khẩu proxy cho profile này")
        if ":" in username or any(ord(c) < 32 for c in username + password):
            raise ValueError
        username.encode("latin-1")
        password.encode("latin-1")
        if parsed.scheme in {"socks5", "socks5h"} and (len(username) > 255 or len(password) > 255):
            raise ValueError
        host = f"[{host}]" if ":" in host else host.encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        raise ProfileError("Proxy không hợp lệ: dùng http://host:port hoặc socks5://host:port") from None
    return {"server": f"{parsed.scheme}://{host}:{port}",
            "username": username, "password": password}


def parse(text: str, *, profile_id: str = "", label: str = "", proxy=None) -> list[dict]:
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise ProfileError("Không đọc được JSON cookie/profile") from None
    if isinstance(data, dict) and "profiles" in data:
        entries = data["profiles"]
    elif isinstance(data, list) and data and all(
            isinstance(item, dict) and "profile_id" in item for item in data):
        entries = data
    else:
        # The owner's normal Cookie-Editor array is accepted without reformatting.
        entries = [{"profile_id": profile_id, "label": label, "proxy": proxy,
                    "session": data}] if not (isinstance(data, dict) and "profile_id" in data) else [data]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PROFILES:
        raise ProfileError(f"JSON phải có từ 1 đến {MAX_PROFILES} profile")
    profiles, ids, sessions, routes = [], set(), set(), set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ProfileError(f"Profile thứ {index} không hợp lệ")
        pid = entry.get("profile_id")
        if not isinstance(pid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", pid):
            raise ProfileError(f"Profile thứ {index}: mã profile gồm 1–64 chữ, số, dấu - hoặc _")
        if pid in ids:
            raise ProfileError("Mã profile bị trùng trong JSON")
        name = entry.get("label") or pid
        if not isinstance(name, str) or len(name) > 100:
            raise ProfileError(f"Profile thứ {index}: tên tối đa 100 ký tự")
        route = _proxy(entry.get("proxy"))
        routing_key = (route["server"], route["username"], route["password"])
        if routing_key in routes:
            raise ProfileError("Một proxy chỉ được gắn với một profile; dùng proxy hoặc thông tin xác thực riêng")
        session_data = entry.get("session")
        if session_data is None:
            cookies = entry.get("cookies")
            if "expires" in entry and not isinstance(entry["expires"], dict):
                raise ProfileError(f"Profile thứ {index}: expires phải là object tên cookie–thời hạn")
            session_data = cookies if isinstance(cookies, list) else {
                "cookies": cookies, "expires": entry.get("expires") or {}}
        try:
            if isinstance(session_data, dict) and isinstance(session_data.get("cookies"), dict):
                if not all(isinstance(v, str) for v in session_data["cookies"].values()):
                    raise ValueError
            session = muse_session.parse(json.dumps(session_data))
            if any(not 0 <= stamp <= 253402300799 for stamp in session["expires"].values()):
                raise ValueError
        except (muse_session.SessionError, TypeError, AttributeError, OverflowError, ValueError):
            raise ProfileError(f"Profile thứ {index}: cookie Muse thiếu, sai định dạng hoặc đã hết hạn") from None
        identity = session["cookies"]["hatch_sess"]
        if not identity.strip() or identity in sessions:
            raise ProfileError("Một phiên Muse chỉ được gắn với một profile")
        if "enabled" in entry and not isinstance(entry["enabled"], bool):
            raise ProfileError(f"Profile thứ {index}: enabled phải là true hoặc false")
        ids.add(pid)
        sessions.add(identity)
        routes.add(routing_key)
        profiles.append({"profile_id": pid, "label": name, "proxy": route,
                         "enabled": entry.get("enabled", True), **session})
    return profiles


def load(settings) -> list[dict]:
    saved = secret_store.load(settings, NAME)
    return saved.get("profiles", []) if saved else []


def summary(profile: dict) -> dict:
    session = muse_session.summary(profile) or {}
    route = profile["proxy"]
    return {"profile_id": profile["profile_id"], "label": profile["label"],
            "enabled": profile.get("enabled", True), "proxy_server": route["server"],
            "proxy_authenticated": bool(route.get("username") or route.get("password")),
            "cookie_count": len(profile["cookies"]), "session_expires": session.get("session_expires"),
            "imported_at": profile.get("imported_at"), "pushed_at": profile.get("pushed_at"),
            "account_id": profile.get("account_id")}


def summaries(settings) -> list[dict]:
    return [summary(p) for p in load(settings)]


def save(settings, text: str, **fields) -> dict:
    incoming = parse(text, **fields)
    with _LOCK:
        existing = {p["profile_id"]: p for p in load(settings)}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for profile in incoming:
            existing[profile["profile_id"]] = {**profile, "imported_at": now, "revision": uuid.uuid4().hex}
        if len(existing) > MAX_PROFILES:
            raise ProfileError(f"Tối đa {MAX_PROFILES} profile đã lưu")
        identities = [p["cookies"]["hatch_sess"] for p in existing.values()]
        if len(set(identities)) != len(identities):
            raise ProfileError("Phiên Muse này đã thuộc một profile khác; dùng đúng mã profile để cập nhật")
        routes = [(p["proxy"]["server"], p["proxy"]["username"], p["proxy"]["password"])
                  for p in existing.values()]
        if len(set(routes)) != len(routes):
            raise ProfileError("Proxy này đã thuộc một profile khác; dùng đúng mã profile để cập nhật")
        secret_store.save(settings, NAME, {"profiles": list(existing.values())})
        return {"imported": len(incoming), "total": len(existing),
                "profiles": [summary(existing[p["profile_id"]]) for p in incoming]}


def push_to_gateway(settings, transport=None) -> dict:
    """Atomically upsert profile routes; never include gateway response secrets."""
    if ((settings.videogen_provider or "").lower() != "muse2api"
            or not settings.videogen_base_url or not settings.videogen_api_key.get_secret_value()):
        raise ProfileError("Cấu hình muse2api và API key trước khi đồng bộ profile")
    with _LOCK:
        profiles = load(settings)
        if not profiles:
            raise ProfileError("Chưa nhập profile Muse")
        payload = [{**{k: p[k] for k in ("profile_id", "label", "proxy", "cookies", "expires", "enabled")},
                    "revision": p.get("revision")} for p in profiles]
        try:
            with httpx.Client(timeout=30, transport=transport) as client:
                response = client.post(settings.videogen_base_url.rstrip("/") + "/admin/accounts/profiles",
                                       headers={"Authorization": "Bearer " + settings.videogen_api_key.get_secret_value()},
                                       json={"profiles": payload})
        except httpx.HTTPError:
            raise ProfileError("Không kết nối được gateway; profile đã lưu, hãy đồng bộ lại khi gateway chạy") from None
        if response.status_code >= 400:
            message = ("Gateway đang dùng account hoặc profile xung đột; chờ tác vụ xong và kiểm tra mã profile"
                       if response.status_code == 409 else "Gateway từ chối profile; kiểm tra cấu hình hoặc phiên bản gateway")
            raise ProfileError(f"{message} (HTTP {response.status_code})")
        try:
            result = response.json()
            accounts = result["profiles"]
            if not isinstance(accounts, list) or len(accounts) != len(profiles):
                raise ValueError
            mapping = {a["profile_id"]: a["account_id"] for a in accounts}
            if set(mapping) != {p["profile_id"] for p in profiles} or not all(
                    isinstance(aid, str) and aid for aid in mapping.values()):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ProfileError("Gateway trả kết quả profile không hợp lệ; chưa xác nhận đồng bộ") from None
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for profile in profiles:
            profile.update(pushed_at=now, account_id=mapping[profile["profile_id"]])
        secret_store.save(settings, NAME, {"profiles": profiles})
        return {"synced": len(profiles), "profiles": [summary(p) for p in profiles]}
