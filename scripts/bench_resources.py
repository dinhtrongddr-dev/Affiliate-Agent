"""Measure RAM and CPU of the app's processes (Windows, no extra packages).

Usage: python scripts/bench_resources.py [--seconds 60] [--out file.json] [--label idle]
Samples every 5 s through PowerShell/CIM and groups processes by role.
"""
import argparse
import json
import subprocess
import time
from datetime import datetime

PS = (
    "Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | "
    "Select-Object IDProcess,PercentProcessorTime | ConvertTo-Json -Compress; "
    "'@@'; "
    "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,WorkingSetSize,CommandLine | "
    "ConvertTo-Json -Compress"
)


def role(name: str, cmd: str) -> str | None:
    name, cmd = (name or "").lower(), (cmd or "").lower()
    if name in ("python.exe", "pythonw.exe"):
        if "agent_core.tray" in cmd:
            return "tray"
        if "agent_core.desktop" in cmd:
            return "desktop_window"
        if "agent_core.cli" in cmd:
            return "app"
        if "uvicorn app:app" in cmd and "18620" in cmd:
            return "muse_gateway"
        if "chatgpt2api" in cmd or "13000" in cmd:
            return "chatgpt2api"
        if "vieneu" in cmd or "tts" in cmd or "speech_check" in cmd:
            return "voice_worker"
        return None
    if name == "chrome.exe":
        return "muse_chrome" if "muse2api" in cmd else "user_chrome"
    if name == "msedgewebview2.exe":
        return "app_window_webview2" if "cache\\webview" in cmd else "webview2_other"
    if name == "ffmpeg.exe":
        return "ffmpeg"
    return None


def sample() -> dict:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", PS], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=60).stdout
    cpu_raw, procs_raw = out.split("@@", 1)
    cpu = {p["IDProcess"]: p["PercentProcessorTime"] for p in json.loads(cpu_raw)}
    groups: dict[str, dict] = {}
    for p in json.loads(procs_raw):
        r = role(p.get("Name"), p.get("CommandLine"))
        if not r:
            continue
        g = groups.setdefault(r, {"count": 0, "mb": 0.0, "cpu": 0.0})
        g["count"] += 1
        g["mb"] += (p.get("WorkingSetSize") or 0) / 1048576
        g["cpu"] += cpu.get(p["ProcessId"], 0) or 0
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--label", default="idle")
    ap.add_argument("--out")
    a = ap.parse_args()
    samples, end = [], time.time() + a.seconds
    while True:
        samples.append(sample())
        if time.time() >= end:
            break
        time.sleep(5)
    roles = sorted({r for s in samples for r in s})
    summary = {}
    for r in roles:
        vals = [s.get(r, {"count": 0, "mb": 0, "cpu": 0}) for s in samples]
        summary[r] = {"procs": max(v["count"] for v in vals),
                      "ram_mb_avg": round(sum(v["mb"] for v in vals) / len(vals)),
                      "ram_mb_max": round(max(v["mb"] for v in vals)),
                      "cpu_pct_avg": round(sum(v["cpu"] for v in vals) / len(vals), 1)}
    result = {"at": datetime.now().isoformat(timespec="seconds"), "label": a.label,
              "seconds": a.seconds, "samples": len(samples), "roles": summary}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
