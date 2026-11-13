"""Bounded server-side client for the optional AddLiveTag data provider.

One method call makes at most one request. No pagination, retries, short-link
resolution, or affiliate-link creation is performed locally.
"""
import math
import re
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

ORIGIN = "https://data.addlivetag.com"
_OFFERS = {
    "products": ("product-offer.php", "products"),
    "shops": ("shop-offer.php", "shops"),
    "campaigns": ("shopee-offer.php", "offers"),
    "shop-products": ("shop-products.php", "products"),
}


class AddLiveError(RuntimeError):
    """Safe to display: provider bodies, credentials and request URLs are omitted."""

    def __init__(self, message: str, retry_after: int = 0, *, code: str = 'provider_error'):
        super().__init__(message)
        self.retry_after = max(0, int(retry_after))
        self.code = code if re.fullmatch(r'[a-z0-9_]{1,40}', code) else 'provider_error'


def item_id_from_reference(value) -> str:
    """Read a positive ID or canonical Vietnamese Shopee product URL without I/O."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise AddLiveError("Mã sản phẩm hoặc link Shopee không hợp lệ.")
    value = str(value).strip()
    if re.fullmatch(r"[0-9]{1,24}", value) and int(value) > 0:
        return str(int(value))
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname in {"shopee.vn", "www.shopee.vn"}
                 and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        valid = False
    if valid:
        match = re.fullmatch(r"/product/([0-9]{1,24})/([0-9]{1,24})/?", parsed.path)
        if not match:
            match = re.fullmatch(r"/[^/]*-i\.([0-9]{1,24})\.([0-9]{1,24})/?", parsed.path)
        if match and int(match[1]) > 0 and int(match[2]) > 0:
            return str(int(match[2]))
    raise AddLiveError("Cần mã sản phẩm hoặc link sản phẩm Shopee đầy đủ hợp lệ.")


def _integer(value, low: int, high: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise AddLiveError(f"{label} phải là số nguyên từ {low} đến {high}.")
    return value


def _tier(base_rate, cap) -> dict:
    params = {"cap": _integer(cap, 0, 1_000_000_000, "Trần hoa hồng")}
    if base_rate is not None:
        try:
            rate = float(base_rate)
        except (TypeError, ValueError):
            raise AddLiveError("Tỷ lệ hoa hồng không hợp lệ.") from None
        if isinstance(base_rate, bool) or not math.isfinite(rate) or not 0 <= rate <= 100:
            raise AddLiveError("Tỷ lệ hoa hồng phải nằm trong khoảng 0–100%.")
        params["base_rate"] = rate / 100 if rate > 1 else rate
    return params


class AddLiveClient:
    # Provider quota is shared by IP; keep cooldown across request-scoped clients.
    _lock = threading.Lock()
    _source_until = 0.0
    _all_until = 0.0

    def __init__(self, api_key: str, transport=None):
        if not isinstance(api_key, str) or not api_key.strip():
            raise AddLiveError("Chưa cấu hình API Key AddLiveTag ở phía máy chủ.")
        if "\n" in api_key or "\r" in api_key:
            raise AddLiveError("API Key AddLiveTag không hợp lệ.")
        self._api_key = api_key.strip()
        self._transport = transport

    def __repr__(self):
        return "AddLiveClient(api_key=<redacted>)"

    @classmethod
    def _wait(cls, source: bool) -> int:
        with cls._lock:
            return max(0, math.ceil(max(cls._all_until,
                                      cls._source_until if source else 0) - time.monotonic()))

    @classmethod
    def _cooldown(cls, seconds: int, *, all_requests: bool = False):
        with cls._lock:
            attr = "_all_until" if all_requests else "_source_until"
            setattr(cls, attr, max(getattr(cls, attr), time.monotonic() + seconds))

    @staticmethod
    def _retry_after(value) -> int:
        try:
            return max(1, min(86400, int(value)))
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(value)
                return max(1, min(86400, math.ceil((date - datetime.now(timezone.utc)).total_seconds())))
            except (ValueError, TypeError, OverflowError):
                return 120

    def _get(self, path: str, params: dict, field: str, *, source: bool = True) -> dict:
        wait = self._wait(source)
        if wait:
            raise AddLiveError("AddLiveTag đang nghỉ theo hạn mức; vui lòng thử lại sau.", wait)
        try:
            with httpx.Client(timeout=httpx.Timeout(45, connect=10), follow_redirects=False,
                              transport=self._transport, trust_env=False) as client:
                response = client.get(ORIGIN + path, params=params,
                                      headers={"X-API-Key": self._api_key, "Accept": "application/json"})
        except httpx.HTTPError:
            raise AddLiveError("Không kết nối được AddLiveTag; chưa tự động thử lại.", code='connection_error') from None
        if response.status_code == 401:
            raise AddLiveError("AddLiveTag từ chối API Key (401); kiểm tra key ở cấu hình máy chủ.", code='http_401')
        if response.status_code == 403:
            raise AddLiveError("AddLiveTag từ chối quyền truy cập (403).", code='http_403')
        if response.status_code == 429:
            wait = self._retry_after(response.headers.get("Retry-After"))
            self._cooldown(wait, all_requests=True)
            raise AddLiveError("AddLiveTag đã chạm hạn mức (429); vui lòng thử lại sau.", wait, code='http_429')
        if response.status_code != 200:
            raise AddLiveError(f"AddLiveTag trả lỗi HTTP {response.status_code}; chưa tự động thử lại.", code=f'http_{response.status_code}')
        try:
            data = response.json()
        except ValueError:
            raise AddLiveError("AddLiveTag trả dữ liệu JSON không hợp lệ.") from None
        if not isinstance(data, dict) or data.get("status") != "success":
            raise AddLiveError("AddLiveTag không trả kết quả thành công hợp lệ.")
        expected = dict if field == "productInfo" else list
        if not isinstance(data.get(field), expected):
            raise AddLiveError("Cấu trúc dữ liệu AddLiveTag không hợp lệ.")
        if expected is list and any(not isinstance(row, dict) for row in data[field]):
            raise AddLiveError("Danh sách dữ liệu AddLiveTag không hợp lệ.")
        if field in {"products", "items"} and path.startswith(("/product-data/", "/price-tracking/")):
            statuses = ({"success", "stale", "skipped", "not_found", "error"}
                        if field == "products" else {"success", "no_data", "error"})
            for row in data[field]:
                if not isinstance(row.get("status"), str) or row["status"] not in statuses:
                    raise AddLiveError("Trạng thái từng sản phẩm AddLiveTag không hợp lệ.")
                if row.get("status") in {"success", "stale"} and field == "products":
                    if not isinstance(row.get("productInfo"), dict):
                        raise AddLiveError("Chi tiết sản phẩm AddLiveTag không hợp lệ.")
        limits = data.get("limits", {})
        if not isinstance(limits, dict):
            raise AddLiveError("Hạn mức dữ liệu AddLiveTag không hợp lệ.")
        cooldown = limits.get("sourceCooldownSeconds", 0)
        if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)) or not math.isfinite(cooldown):
            raise AddLiveError("Hạn mức dữ liệu AddLiveTag không hợp lệ.")
        if limits.get("sourceRateLimited") or cooldown > 0:
            self._cooldown(max(120 if limits.get("sourceRateLimited") else 1,
                               min(86400, math.ceil(cooldown))))
        return data

    def product(self, reference: str, *, base_rate=None, cap=40000) -> dict:
        params = _tier(base_rate, cap)
        try:
            params["item_id"] = item_id_from_reference(reference)
        except AddLiveError:
            try:
                parsed = urlsplit(reference)
                valid = (parsed.scheme == "https" and parsed.hostname in {"s.shopee.vn", "vn.shp.ee"}
                         and parsed.port in (None, 443) and not parsed.username and not parsed.password
                         and parsed.path not in {"", "/"})
            except (ValueError, TypeError, AttributeError):
                valid = False
            if not valid:
                raise AddLiveError("Link hoặc mã sản phẩm Shopee không hợp lệ.") from None
            params["url"] = reference
        return self._get("/product-data/product-data.php", params, "productInfo")

    @staticmethod
    def _check_rows(data: dict, field: str, expected: list[str]) -> dict:
        """Reject silently shortened/reordered batches before any caller imports them."""
        rows = data[field]
        for name, count in (("requested", len(expected)), ("returned", len(rows))):
            if name in data:
                value = data[name]
                if isinstance(value, bool) or not isinstance(value, int) or value != count:
                    raise AddLiveError("Số lượng kết quả AddLiveTag không khớp yêu cầu.")
        if len(rows) != len(expected):
            raise AddLiveError("AddLiveTag trả thiếu hoặc thừa sản phẩm; chưa nhập dữ liệu.")
        for row, item_id in zip(rows, expected, strict=True):
            if row.get("status") not in {"success", "stale"}:
                continue
            identities = [row["itemId"]] if "itemId" in row else []
            product = row.get("productInfo") if field == "products" else row.get("product")
            if isinstance(product, dict) and "itemId" in product:
                identities.append(product["itemId"])
            for identity in identities:
                if item_id_from_reference(identity) != item_id:
                    raise AddLiveError("Mã sản phẩm AddLiveTag không khớp yêu cầu; chưa nhập dữ liệu.")
        return data

    def batch(self, references: list[str], *, base_rate=None, cap=40000,
              cache_only=False, max_api=10) -> dict:
        if not isinstance(references, list) or not 1 <= len(references) <= 100:
            raise AddLiveError("Mỗi lô cần từ 1 đến 100 sản phẩm; không tự chia lô.")
        if not isinstance(cache_only, bool):
            raise AddLiveError("cache_only phải là giá trị đúng/sai.")
        budget = _integer(max_api, 0, 50, "Ngân sách nguồn")
        item_ids = list(map(item_id_from_reference, references))
        params = {**_tier(base_rate, cap), "item_ids": ",".join(item_ids),
                  "cache_only": int(cache_only), "max_api": 0 if cache_only else budget}
        data = self._get("/product-data/product-data-batch.php", params, "products",
                         source=not cache_only and budget > 0)
        return self._check_rows(data, "products", item_ids)

    def history(self, item_ids: list[str], *, days=90, base_rate=None, cap=40000) -> dict:
        if not isinstance(item_ids, list) or not 1 <= len(item_ids) <= 50:
            raise AddLiveError("Lịch sử cần từ 1 đến 50 sản phẩm.")
        normalized = list(map(item_id_from_reference, item_ids))
        params = {**_tier(base_rate, cap), "item_ids": ",".join(normalized),
                  "days": _integer(days, 1, 730, "Số ngày"), "type": "both", "changes_only": 1}
        data = self._get("/price-tracking/history.php", params, "items", source=False)
        return self._check_rows(data, "items", normalized)

    def offers(self, kind="products", *, keyword="", page=1, limit=20, sort_type=1, shop_id="") -> dict:
        if not isinstance(kind, str) or kind not in _OFFERS:
            raise AddLiveError("Loại danh sách AddLiveTag không hợp lệ.")
        if not isinstance(keyword, str) or len(keyword) > 300:
            raise AddLiveError("Từ khóa phải là chuỗi tối đa 300 ký tự.")
        endpoint, field = _OFFERS[kind]
        params = {"page": _integer(page, 1, 10000, "Trang"),
                  "limit": _integer(limit, 1, 50, "Số kết quả"),
                  "sortType": _integer(sort_type, 1, 6, "Kiểu sắp xếp")}
        if kind == "shop-products":
            if not re.fullmatch(r"[0-9]{1,24}", str(shop_id)) or int(shop_id) <= 0:
                raise AddLiveError("Cần mã shop dạng số hợp lệ.")
            params["shopId"] = str(int(shop_id))
        else:
            params["keyword"] = keyword.strip()
        return self._get("/offers/" + endpoint, params, field)
