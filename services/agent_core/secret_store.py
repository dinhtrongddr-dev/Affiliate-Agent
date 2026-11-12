"""Small encrypted secret store for session material (e.g. a Muse login cookie export).

On Windows the bytes are encrypted with DPAPI (CryptProtectData, current-user scope), so
the file is only readable by the same Windows account on the same machine. Other platforms
are refused rather than silently storing plain text."""
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path

ENTROPY = b"affiliate-agent/secret-store/v1"


class SecretStoreError(RuntimeError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _crypt(data: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise SecretStoreError("Kho bí mật mã hoá chỉ hỗ trợ Windows (DPAPI)")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, entropy, out = _blob(data), _blob(ENTROPY), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if protect:
        ok = fn(ctypes.byref(src), "affiliate-agent", ctypes.byref(entropy), None, None, 0x1,
                ctypes.byref(out))
    else:
        ok = fn(ctypes.byref(src), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise SecretStoreError(f"DPAPI lỗi {ctypes.GetLastError()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _path(settings, name: str) -> Path:
    if not name.replace("_", "").isalnum():
        raise SecretStoreError("Tên bí mật không hợp lệ")
    return settings.project_root / "state" / "secrets" / f"{name}.bin"


def save(settings, name: str, value: dict) -> None:
    path = _path(settings, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(_crypt(json.dumps(value).encode("utf-8"), True))
    tmp.replace(path)


def load(settings, name: str) -> dict | None:
    path = _path(settings, name)
    if not path.is_file():
        return None
    return json.loads(_crypt(path.read_bytes(), False).decode("utf-8"))


def delete(settings, name: str) -> bool:
    path = _path(settings, name)
    if path.is_file():
        path.unlink()
        return True
    return False
