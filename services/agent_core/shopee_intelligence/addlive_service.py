"""Explicit AddLive imports; provider history never becomes sales evidence."""
import json
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

from ..db import connect, now_iso
from .. import catalog as video_catalog
from .catalog import get_product, init_intelligence_schema, save_product
from .unified_catalog import mirror_product


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def _rate(info, percent, fraction):
    explicit = _number(info.get(percent))
    return explicit if explicit is not None else (
        _number(info.get(fraction)) * 100 if _number(info.get(fraction)) is not None else None)


def normalize_product(info, *, data_source='', warning='', existing=None):
    """Keep absent evidence absent, and preserve owner link/rights decisions."""
    existing = existing or {}
    link = str(info.get('productLink') or '')
    parsed = urlparse(link)
    match = re.search(r'/product/(\d+)/(\d+)(?:/|$)', parsed.path)
    if not match:
        match = re.search(r'-i\.(\d+)\.(\d+)(?:/|$)', parsed.path)
    if parsed.scheme != 'https' or parsed.hostname != 'shopee.vn' or not match:
        raise ValueError('AddLive product requires a canonical Shopee product URL.')
    shop_id, item_id = match.groups()
    if info.get('itemId') is not None and str(info['itemId']) != item_id:
        raise ValueError('AddLive product ID does not match its URL.')
    name = str(info.get('productName') or '').strip()
    if not name:
        raise ValueError('AddLive product name is missing.')
    base = _rate(info, 'shopeeRatePercent', 'shopeeRate')
    seller = _rate(info, 'sellerRatePercent', 'sellerRate')
    total = _number(info.get('totalRatePercent'))
    if total is None and base is not None and seller is not None:
        total = base + seller
    return {
        'product_id': existing.get('product_id') or item_id,
        'item_id': item_id, 'shop_id': shop_id, 'product_name': name,
        'product_url': f'https://shopee.vn/product/{shop_id}/{item_id}',
        'shop_name': info.get('shopName'), 'current_price': _number(info.get('price')),
        'total_sold': _number(info.get('sales')), 'rating_star': _number(info.get('rating')),
        'rating_count': _number(info.get('ratingCount')),
        # Rights attach to the approved asset, not to an arbitrary replacement URL.
        'image_url': (existing.get('image_url') if existing.get('media_usage_rights') in
                      {'owned', 'licensed', 'permission', 'official'} and existing.get('image_url')
                      else info.get('imageUrl')),
        'base_commission_rate': base, 'xtra_commission_rate': seller,
        'commission_total_rate': total, 'commission_amount': _number(info.get('commission')),
        'affiliate_link': existing.get('affiliate_link'),
        'media_usage_rights': existing.get('media_usage_rights') or 'unknown',
        'addlive': {'dataSource': data_source or info.get('dataSource', ''),
                    'warning': warning, 'priceStatistics': info.get('priceStatistics'),
                    'shopeeRateSource': info.get('shopeeRateSource'),
                    'sellerComFinal': info.get('sellerComFinal'),
                    'shopeeComFinal': info.get('shopeeComFinal'), 'isCapped': info.get('isCapped')},
    }


def _newer(current, candidate) -> bool:
    """True when the catalog's timestamp is strictly newer than the incoming cache timestamp."""
    def parse(value):
        try:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    a, b = parse(current) if current else None, parse(candidate) if candidate else None
    return bool(a and b and a > b)


def _persist(db_path, row, *, affiliate_id='', sub_id=''):
    info = row.get('productInfo') or {}
    item_id = normalize_product(info)['item_id']
    existing = get_product(db_path, item_id)
    if not existing:
        # Custom product IDs can predate this source integration.
        with connect(db_path) as conn:
            match = conn.execute('SELECT product_id FROM shopee_products WHERE item_id=? AND shop_id=? '
                                 'ORDER BY updated_at DESC LIMIT 1', (item_id, normalize_product(info)['shop_id'])).fetchone()
        if match:
            existing = get_product(db_path, match['product_id'])
    video_catalog.ensure_schema(db_path)
    legacy = video_catalog.get_product(db_path, 'shopee:' + item_id) or {}
    if legacy.get('affiliate_link') and not (existing or {}).get('affiliate_link'):
        existing = {**(existing or {}), 'affiliate_link': legacy['affiliate_link']}
    source = row.get('dataSource') or info.get('dataSource') or ''
    warning = row.get('warning') or info.get('warning') or ''
    product = normalize_product(info, data_source=source, warning=warning, existing=existing)
    if affiliate_id and not product.get('affiliate_link'):
        product['affiliate_link'] = 'https://s.shopee.vn/an_redir?' + urlencode({
            'origin_link': product['product_url'], 'affiliate_id': affiliate_id, 'sub_id': sub_id})
    # Stale rows (older cache: AddLiveTag's per-request fresh budget is only 10 items) carry full
    # details and are imported as cache data with the provider's own timestamp, never as a fresh
    # observation. Explicit stale flags override even an inconsistent success status.
    stale = row.get('status') == 'stale' or bool(row.get('stale')) or source == 'db_stale'
    usable = (row.get('status') in {'success', 'stale'} and source in {'api', 'db', 'db_stale'}
              and not warning)
    if not usable:
        return {**row, 'imported': False, 'normalized': product, 'data_source': source,
                'warnings': [warning] if warning else []}
    if source == 'api' and not stale:
        # Observation uniqueness must not silently collapse separate same-second
        # retrievals. Cache imports deliberately never take this path.
        product['observed_at'] = datetime.now(timezone.utc).isoformat(timespec='microseconds')
        saved = save_product(db_path, product, source_type='addlive_api')
    else:
        # Cache may populate current details, but its retrieval time is not a new observation.
        # New cache records have no sales observation until a fresh fetch occurs.
        stamp = now_iso()
        try:
            source_time = datetime.fromisoformat(str(info.get('lastUpdate') or '').replace('Z', '+00:00'))
            if source_time.tzinfo is None:
                source_time = source_time.replace(tzinfo=timezone(timedelta(hours=7)))
            source_stamp = source_time.astimezone(timezone.utc).isoformat()
        except ValueError:
            source_stamp = (existing or {}).get('updated_at') or ''
            if stale and existing:
                source_stamp = ''   # undated old cache: never replaces what the catalog has
        if existing and (_newer(existing.get('updated_at'), source_stamp) or (stale and not source_stamp)):
            # older cache never overwrites fresher catalog data; the product is already there
            return {**row, 'itemId': product['item_id'], 'imported': True, 'kept_newer': True,
                    'normalized': product, 'product': existing, 'data_source': source, 'warnings': []}
        columns = [k for k in product if k != 'addlive']
        values = {k: product[k] for k in columns}
        values.update(source_type='addlive_db', observed_at=(existing or {}).get('observed_at') or source_stamp,
                      created_at=stamp, updated_at=source_stamp)
        updates = ','.join(f'{k}=COALESCE(excluded.{k},shopee_products.{k})'
                           for k in values if k not in {'product_id', 'created_at', 'observed_at'})
        with connect(db_path) as conn:
            conn.execute(f"INSERT INTO shopee_products ({','.join(values)}) VALUES ({','.join('?' for _ in values)}) "
                         f'ON CONFLICT(product_id) DO UPDATE SET {updates}', list(values.values()))
        saved = get_product(db_path, product['product_id'])
    mirror_product(db_path, product['product_id'])
    return {**row, 'itemId': product['item_id'], 'imported': True, 'normalized': product,
            'product': saved, 'data_source': source, 'warnings': []}


def _import_response(settings, response, *, affiliate_id='', sub_id=''):
    init_intelligence_schema(settings.database_path)
    with connect(settings.database_path) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS addlive_import_evidence '
                     '(id INTEGER PRIMARY KEY, retrieved_at TEXT NOT NULL, response_json TEXT NOT NULL)')
        conn.execute('INSERT INTO addlive_import_evidence (retrieved_at,response_json) VALUES (?,?)',
                     (now_iso(), json.dumps(response, ensure_ascii=False)))
    results = []
    rows = response.get('products') or []
    if response.get('status') == 'success':
        for row in rows:
            if not isinstance(row, dict):
                continue
            row = {**row}
            for flag in ('warning', 'stale', 'dataSource'):
                if response.get(flag) and flag not in row:
                    row[flag] = response[flag]
            if row.get('status') not in {'success', 'stale'} or not isinstance(row.get('productInfo'), dict):
                results.append({**row, 'imported': False, 'data_source': row.get('dataSource', ''),
                                'warnings': [str(row['warning'])] if row.get('warning') else []})
                continue
            try:
                results.append(_persist(settings.database_path, row, affiliate_id=affiliate_id, sub_id=sub_id))
            except ValueError as exc:
                results.append({**row, 'imported': False, 'import_error': str(exc),
                                'data_source': row.get('dataSource', ''), 'warnings': []})
    return {**response, 'imported': sum(bool(r.get('imported')) for r in results),
            'results': results, 'summary': response.get('summary') or {},
            'limits': response.get('limits') or {}}


def import_batch(settings, client, references, *, base_rate=None, cap=40000,
                 cache_only=False, max_api=10, affiliate_id='', sub_id=''):
    response = client.batch(references, base_rate=base_rate, cap=cap,
                            cache_only=cache_only, max_api=max_api)
    return _import_response(settings, response, affiliate_id=affiliate_id, sub_id=sub_id)


def import_product(settings, client, reference, *, base_rate=None, cap=40000,
                   affiliate_id='', sub_id=''):
    response = client.product(reference, base_rate=base_rate, cap=cap)
    result = _import_response(settings, {**response, 'products': [response]},
                              affiliate_id=affiliate_id, sub_id=sub_id)
    return result


def retrieve_history(client, item_ids, days=90, base_rate=None, cap=40000):
    """Return unchanged provider points, including recorded/suspect markers."""
    response = client.history(item_ids, days=days, base_rate=base_rate, cap=cap)
    warnings = []
    if base_rate is None:
        warnings.append('Lịch sử hoa hồng chưa được chuẩn hóa theo mức tài khoản của chủ sở hữu.')
    if (response.get('limits') or {}).get('truncated'):
        warnings.append('Nhà cung cấp đã cắt bớt dữ liệu lịch sử do giới hạn số điểm.')
    for key in ('notice', 'warning'):
        if response.get(key):
            warnings.append(str(response[key]))
    for item in response.get('items') or []:
        for warning in (item.get('warning'), (item.get('commission') or {}).get('warning')):
            if warning and str(warning) not in warnings:
                warnings.append(str(warning))
    return {**response, 'warnings': warnings}
