"""Time each UI page (time to first byte and HTML size), median of N runs.

Usage: python scripts/bench_pages.py [--base http://127.0.0.1:8765] [--runs 3] [--out file.json]
"""
import argparse
import json
import statistics
import time
from datetime import datetime

import httpx

PAGES = ["/", "/create", "/jobs", "/trends", "/china", "/catalog", "/generation", "/analytics",
         "/performance", "/tasks", "/settings", "/logs"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out")
    a = ap.parse_args()
    rows = {}
    with httpx.Client(base_url=a.base, timeout=180, trust_env=False, follow_redirects=True) as c:
        for page in PAGES:
            times, size, code = [], 0, 0
            for _ in range(a.runs):
                t = time.perf_counter()
                r = c.get(page)
                times.append(time.perf_counter() - t)
                size, code = len(r.content), r.status_code
            rows[page] = {"status": code, "median_s": round(statistics.median(times), 3),
                          "max_s": round(max(times), 3), "kb": round(size / 1024)}
            print(f"{page:14} {code} {rows[page]['median_s']:>8.3f}s {rows[page]['kb']:>6} KB")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"at": datetime.now().isoformat(timespec="seconds"), "runs": a.runs, "pages": rows},
                      f, indent=1)


if __name__ == "__main__":
    main()
