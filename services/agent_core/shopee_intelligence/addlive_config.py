"""Local DPAPI configuration for the AddLiveTag data connection."""
import math
import re
import threading

from .. import secret_store

NAME = "addlive_api"
_LOCK = threading.RLock()
DEFAULTS = {"api_key": "", "base_rate": None, "cap": 40000, "affiliate_id": "", "sub_id": ""}


def load(settings) -> dict:
    return DEFAULTS | (secret_store.load(settings, NAME) or {})


def summary(settings) -> dict:
    data = load(settings)
    return {k: v for k, v in data.items() if k != "api_key"} | {"configured": bool(data["api_key"])}


def save(settings, values: dict) -> dict:
    with _LOCK:
        current = load(settings)
        key = values.get("api_key")
        if key:
            if (not isinstance(key, str) or len(key) > 512
                    or not key.strip() or any(not 33 <= ord(c) <= 126 for c in key.strip())):
                raise ValueError("API key không hợp lệ")
            current["api_key"] = key.strip()
        if "base_rate" in values:
            rate = values["base_rate"]
            if rate is not None:
                if isinstance(rate, bool):
                    raise ValueError("Hoa hồng cơ bản phải nằm trong khoảng 0–100%")
                rate = float(rate)
                if not math.isfinite(rate) or not 0 <= rate <= 100:
                    raise ValueError("Hoa hồng cơ bản phải nằm trong khoảng 0–100%")
            current["base_rate"] = rate
        if "cap" in values:
            cap = values["cap"]
            if isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= 1_000_000_000:
                raise ValueError("Trần hoa hồng phải là số nguyên VND không âm")
            current["cap"] = cap
        for field in ("affiliate_id", "sub_id"):
            if field not in values:
                continue
            text = values[field]
            pattern = r"[0-9]{0,30}" if field == "affiliate_id" else r"[A-Za-z0-9_]*(?:-[A-Za-z0-9_]*){0,4}"
            if not isinstance(text, str) or len(text) > 200 or not re.fullmatch(pattern, text):
                raise ValueError("Affiliate ID phải là số; sub_id có tối đa 5 phần chữ/số/_ ngăn bằng dấu -")
            current[field] = text
        secret_store.save(settings, NAME, current)
        return {k: v for k, v in current.items() if k != "api_key"} | {"configured": bool(current["api_key"])}
