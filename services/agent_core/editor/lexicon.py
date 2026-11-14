"""How the Vietnamese voice should say English words, brands and units.

Only the spoken text changes; subtitles keep the original spelling. Owners can add or override
entries in ``config/pronunciations.json`` ({"từ gốc": "cách đọc"}).
"""
import json
import re
from pathlib import Path

DEFAULT = {
    "microfiber": "mai-crô-phai-bơ", "usb": "u ét bê", "type-c": "tai xê", "type c": "tai xê",
    "bluetooth": "blu-tút", "wifi": "wai-phai", "wi-fi": "wai-phai", "iphone": "ai-phôn",
    "samsung": "sam-sung", "xiaomi": "xiao-mi", "oppo": "óp-pô", "shopee": "sốp-pi",
    "tiktok": "tích-tóc", "review": "ri-viu", "combo": "com-bô", "set": "sét", "mini": "mi-ni",
    "pin": "pin", "led": "lét", "ok": "ô kê", "vs": "với", "pro": "prô", "max": "mắc",
    "cm": "xen-ti-mét", "mm": "mi-li-mét", "kg": "ki-lô-gam", "ml": "mi-li-lít", "mah": "mi-li-am-pe giờ",
    "w": "oát", "%": " phần trăm", "k": " nghìn",
}
_UNIT = re.compile(r"(\d)\s*(cm|mm|kg|ml|mah|w)\b", re.I)
_THOUSAND = re.compile(r"(\d+)\s*k\b", re.I)


def load(project_root: Path | None = None) -> dict[str, str]:
    table = dict(DEFAULT)
    if project_root:
        custom = Path(project_root) / "config" / "pronunciations.json"
        if custom.is_file():
            try:
                table.update({str(k).lower(): str(v) for k, v in json.loads(
                    custom.read_text(encoding="utf-8")).items()})
            except (ValueError, AttributeError):
                pass
    return table


def spoken(text: str, table: dict[str, str] | None = None) -> str:
    table = table or DEFAULT
    out = _UNIT.sub(lambda m: f"{m.group(1)} {table.get(m.group(2).lower(), m.group(2))}", text)
    out = _THOUSAND.sub(lambda m: f"{m.group(1)}{table.get('k', ' nghìn')}", out)
    out = out.replace("%", table.get("%", " phần trăm"))
    words = sorted((k for k in table if k not in {"%", "k", "w", "cm", "mm", "kg", "ml", "mah"}),
                   key=len, reverse=True)
    if words:
        pattern = re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in words) + r")(?![\w-])", re.I)
        out = pattern.sub(lambda m: table[m.group(1).lower()], out)
    return " ".join(out.split())
