"""Shopee Affiliate Open API (Vietnam) client.

Request signing follows the Shopee Affiliate Open API spec (also used by the
MIT-licensed shopee-skill upstream in vendor/shopee-skill):
    signature = SHA256(app_id + timestamp + payload + secret)
Credentials stay in config/shopee-skill/credentials.json on this machine.
"""
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

DEFAULT_BASE_URL = "https://open-api.affiliate.shopee.vn/graphql"

# productOfferV2 sortType values
SORT_RELEVANCE = 1
SORT_ITEM_SOLD = 2
SORT_PRICE_DESC = 3
SORT_PRICE_ASC = 4
SORT_COMMISSION = 5

BASE_FIELDS = "productName itemId commissionRate priceMin offerLink imageUrl"
EXTRA_FIELDS = "sales ratingStar shopName productLink priceMax"


class ShopeeAffiliateError(RuntimeError):
    pass


def _is_shopee_vn(url: str) -> bool:
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host in {"shopee.vn", "shope.ee"} or host.endswith(".shopee.vn"))


class ShopeeAffiliateClient:
    def __init__(self, settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.credentials_path = Path(settings.shopee_skill_home) / "credentials.json"
        self._transport = transport
        self._extra_fields_supported = True

    # ------------------------------------------------------------ credentials
    def _load(self) -> dict:
        if not self.credentials_path.is_file():
            return {}
        try:
            data = json.loads(self.credentials_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ShopeeAffiliateError(f"Cannot read {self.credentials_path}: {exc}") from exc
        return data.get("shopee_affiliate") or {}

    def credentials_status(self) -> dict:
        section = self._load()
        missing = [k for k in ("app_id", "secret") if not str(section.get(k, "")).strip()]
        return {
            "ok": True,
            "path": str(self.credentials_path),
            "exists": self.credentials_path.is_file(),
            "sections": {"shopee_affiliate": {"configured": not missing, "missing": missing}},
        }

    def save_credentials(self, app_id: str, secret: str) -> None:
        data = {}
        if self.credentials_path.is_file():
            try:
                data = json.loads(self.credentials_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                data = {}
        section = data.setdefault("shopee_affiliate", {})
        if app_id:
            section["app_id"] = app_id.strip()
        if secret:
            section["secret"] = secret.strip()
        section.setdefault("base_url", DEFAULT_BASE_URL)
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        self.credentials_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # ------------------------------------------------------------ transport
    def _graphql(self, query: str, variables: dict) -> dict:
        if not self.settings.shopee_affiliate_enabled:
            raise ShopeeAffiliateError("Shopee Affiliate API is disabled in local settings.")
        cfg = self._load()
        app_id, secret = str(cfg.get("app_id", "")).strip(), str(cfg.get("secret", "")).strip()
        if not app_id or not secret:
            raise ShopeeAffiliateError("Shopee Affiliate App ID or Secret is missing.")
        payload = json.dumps({"query": query, "variables": variables},
                             separators=(",", ":"), ensure_ascii=False)
        ts = int(time.time())
        signature = hashlib.sha256(f"{app_id}{ts}{payload}{secret}".encode()).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={app_id}, Timestamp={ts}, Signature={signature}",
        }
        url = cfg.get("base_url") or DEFAULT_BASE_URL
        try:
            with httpx.Client(timeout=30, transport=self._transport) as client:
                response = client.post(url, content=payload.encode("utf-8"), headers=headers)
        except httpx.HTTPError as exc:
            raise ShopeeAffiliateError(f"Shopee Affiliate API unreachable: {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise ShopeeAffiliateError(
                f"Shopee Affiliate API returned HTTP {response.status_code}: {response.text[:300]}"
            ) from exc
        if response.status_code != 200 or data.get("errors"):
            raise ShopeeAffiliateError(
                f"Shopee Affiliate API error (HTTP {response.status_code}): "
                f"{json.dumps(data.get('errors') or data, ensure_ascii=False)[:600]}"
            )
        return data.get("data") or {}

    # ------------------------------------------------------------ API
    def search_offers(self, keyword: str = "", limit: int = 20,
                      sort_type: int = SORT_RELEVANCE, page: int = 1) -> list[dict]:
        if not keyword.strip() and sort_type == SORT_RELEVANCE:
            raise ValueError("A product search keyword is required")
        variables = {"keyword": keyword.strip(), "limit": int(limit),
                     "sortType": int(sort_type), "page": int(page)}
        fields = BASE_FIELDS + (" " + EXTRA_FIELDS if self._extra_fields_supported else "")
        query = (
            "query ($keyword: String, $limit: Int, $sortType: Int, $page: Int) {"
            " productOfferV2(keyword: $keyword, limit: $limit, sortType: $sortType, page: $page)"
            " { nodes { " + fields + " } } }"
        )
        try:
            data = self._graphql(query, variables)
        except ShopeeAffiliateError as exc:
            # Older API versions reject unknown fields; retry with the documented core set.
            if self._extra_fields_supported and "Cannot query field" in str(exc):
                self._extra_fields_supported = False
                return self.search_offers(keyword, limit, sort_type, page)
            raise
        return ((data.get("productOfferV2") or {}).get("nodes")) or []

    def hot_offers(self, keyword: str = "", limit: int = 20) -> list[dict]:
        """Best-selling offers (optionally within a keyword)."""
        return self.search_offers(keyword, limit=limit, sort_type=SORT_ITEM_SOLD)

    def create_short_link(self, product_url: str, sub_id: str = "") -> str:
        if not _is_shopee_vn(product_url):
            raise ValueError("Only HTTPS Shopee Vietnam product URLs are accepted")
        sub_ids = [sub_id[:50]] if sub_id else []
        query = ("mutation ($input: GenerateShortLinkInput!) {"
                 " generateShortLink(input: $input) { shortLink } }")
        data = self._graphql(query, {"input": {"originUrl": product_url, "subIds": sub_ids}})
        link = (data.get("generateShortLink") or {}).get("shortLink")
        if not link:
            raise ShopeeAffiliateError("Shopee returned no affiliate short link")
        if not _is_shopee_vn(link):
            raise ShopeeAffiliateError("Shopee returned a link outside the Shopee Vietnam domain")
        return str(link)

    def conversion_report(self, start: int, end: int, limit: int = 500) -> list[dict]:
        query = (
            "query ($start: Int, $end: Int, $limit: Int) {"
            " conversionReport(purchaseTimeStart: $start, purchaseTimeEnd: $end, limit: $limit)"
            " { nodes { conversionId purchaseTime totalCommission"
            " orders { orderId items { itemId itemName } } utmContent } } }"
        )
        try:
            data = self._graphql(query, {"start": int(start), "end": int(end), "limit": limit})
        except ShopeeAffiliateError as exc:
            if "Cannot query field" not in str(exc):
                raise
            query = (
                "query ($start: Int, $end: Int) {"
                " conversionReport(purchaseTimeStart: $start, purchaseTimeEnd: $end)"
                " { nodes { conversionId purchaseTime totalCommission orderId } } }"
            )
            data = self._graphql(query, {"start": int(start), "end": int(end)})
        return ((data.get("conversionReport") or {}).get("nodes")) or []
