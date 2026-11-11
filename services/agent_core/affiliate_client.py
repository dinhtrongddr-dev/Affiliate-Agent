import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


class ShopeeAffiliateError(RuntimeError):
    pass


class ShopeeAffiliateClient:
    def __init__(self, settings):
        self.settings = settings
        self.script_dir = settings.project_root / "vendor" / "shopee-skill" / "scripts"
        self.script = self.script_dir / "shopee_affiliate.py"
        self.config_check = self.script_dir / "config.py"
        if not self.script.is_file():
            raise FileNotFoundError(self.script)

    def _run(self, *args: str) -> dict:
        if not self.settings.shopee_affiliate_enabled:
            raise ShopeeAffiliateError("Shopee Affiliate API is disabled in local settings.")
        env = os.environ.copy()
        env["SHOPEE_SKILL_HOME"] = str(self.settings.shopee_skill_home)
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.script_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        payload = None
        for line in reversed(lines):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        if result.returncode != 0 or not isinstance(payload, dict) or not payload.get("ok"):
            detail = payload or {"stdout": result.stdout[-1000:], "stderr": result.stderr[-1000:]}
            raise ShopeeAffiliateError(f"Shopee Affiliate API command failed: {detail}")
        return payload

    def credentials_status(self) -> dict:
        env = os.environ.copy()
        env["SHOPEE_SKILL_HOME"] = str(self.settings.shopee_skill_home)
        result = subprocess.run(
            [sys.executable, str(self.config_check), "--check", "--json"],
            cwd=str(self.script_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        if result.returncode not in (0, 2):
            raise ShopeeAffiliateError(result.stderr or result.stdout)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def search_offers(self, keyword: str, limit: int = 20) -> list[dict]:
        if not keyword.strip():
            raise ValueError("A product search keyword is required")
        payload = self._run("offers", "--keyword", keyword, "--limit", str(limit))
        return payload.get("offers") or []

    def create_short_link(self, product_url: str, sub_id: str = "") -> str:
        parsed = urlparse(product_url)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not (host == "shopee.vn" or host.endswith(".shopee.vn"))
        ):
            raise ValueError("Only HTTPS Shopee Vietnam product URLs are accepted")
        args = ["link", "--url", product_url]
        if sub_id:
            args.extend(["--sub-id", sub_id])
        payload = self._run(*args)
        link = payload.get("short_link")
        if not link:
            raise ShopeeAffiliateError("Shopee returned no affiliate short link")
        short_url = urlparse(str(link))
        short_host = (short_url.hostname or "").lower()
        if (
            short_url.scheme != "https"
            or not (short_host == "shopee.vn" or short_host.endswith(".shopee.vn"))
        ):
            raise ShopeeAffiliateError("Shopee returned a link outside the Shopee Vietnam domain")
        return str(link)
