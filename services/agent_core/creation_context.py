"""Bounded, read-only product evidence for creative planning. Never fetches URLs."""
import json
import sqlite3
from pathlib import Path


def _read(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []


def build_product_context(settings, item_id, *, experience=None):
    item_id = str(item_id).strip()
    if not item_id.isdigit() or len(item_id) > 30:
        raise ValueError('Mã sản phẩm không hợp lệ.')
    current = legacy = raw = {}
    evidence_id = retrieved_at = None
    path = Path(settings.database_path)
    if path.is_file():
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = _read(conn, 'SELECT * FROM shopee_products WHERE item_id=? LIMIT 1', (item_id,))
            current = dict(rows[0]) if rows else {}
            rows = _read(conn, 'SELECT * FROM products WHERE item_id=? AND platform=? LIMIT 1',
                         (item_id, 'shopee'))
            legacy = dict(rows[0]) if rows else {}
            # Read at most 50 small records. Oversized old payloads are skipped in SQL.
            rows = _read(conn, 'SELECT id,retrieved_at,response_json FROM addlive_import_evidence '
                         'WHERE length(response_json)<=1048576 ORDER BY id DESC LIMIT 50')
            for row in rows:
                try:
                    response = json.loads(row['response_json'])
                except (ValueError, TypeError):
                    continue
                if not isinstance(response, dict):
                    continue
                candidates = response.get('products') or [response]
                if not isinstance(candidates, list):
                    continue
                for candidate in candidates[:100]:
                    if not isinstance(candidate, dict):
                        continue
                    info = candidate.get('productInfo') or {}
                    if not isinstance(info, dict):
                        continue
                    if str(info.get('itemId')) == item_id:
                        raw = info
                        evidence_id, retrieved_at = row['id'], row['retrieved_at']
                        raw = {**raw, '_status': candidate.get('status', response.get('status')),
                               '_warning': candidate.get('warning') or response.get('warning'),
                               '_source': candidate.get('dataSource') or info.get('dataSource'),
                               '_stale': candidate.get('stale') or response.get('stale')}
                        break
                if evidence_id is not None:
                    break

    def value(field, old=None):
        found = current.get(field)
        return found if found is not None else legacy.get(old or field)

    name = value('product_name', 'name')
    product_url = value('product_url', 'product_link')
    stats = raw.get('priceStats') or raw.get('priceStatistics')
    stats = {k: stats[k] for k in ('min', 'max', 'avg', 'first', 'last', 'isLowest', 'isHighest',
             'priceChange7d', 'priceChange30d', 'change7d', 'change30d', 'dayCount')
             if isinstance(stats, dict) and k in stats and isinstance(stats[k], (int, float, bool, type(None)))}
    category_ids = raw.get('catIds') if isinstance(raw.get('catIds'), list) else []
    category_path = raw.get('catPath') if isinstance(raw.get('catPath'), list) else []
    facts = {'price': value('current_price', 'price'), 'currency': current.get('currency', 'VND'),
             'sales': value('total_sold', 'sold'), 'rating': value('rating_star'),
             'rating_count': value('rating_count'),
             'commission_rate_percent': value('commission_total_rate', 'commission_rate'),
             'commission_amount': value('commission_amount', 'commission'),
             'category_ids': [x for x in category_ids[:10] if isinstance(x, int)],
             'category_id': raw.get('catId') if isinstance(raw.get('catId'), int) else None,
             'category_name': str(raw['catName'])[:200] if raw.get('catName') else None,
             'category_path': [str(x)[:200] for x in category_path[:10]],
             'category_names_inferred': bool(raw.get('catName') or raw.get('catPath')),
             'price_statistics': stats or None}
    images = []
    approved = current.get('media_usage_rights') in {'owned', 'licensed', 'permission', 'official'}
    # Owner decision: listing photos from the data API are used to keep the product accurate.
    listing_ok = bool(getattr(settings, 'use_listing_images', False))
    stored_image = value('image_url')
    if stored_image:
        images.append({'url': stored_image, 'role': 'primary', 'source': 'catalog',
                       'rights': current.get('media_usage_rights') or 'unknown',
                       'approvedForRender': (approved or listing_ok)
                       and stored_image == current.get('image_url')})
    if raw.get('imageUrl') and raw['imageUrl'] != stored_image:
        images.append({'url': raw['imageUrl'], 'role': 'primary', 'source': 'addlive',
                       'rights': 'listing' if listing_ok else 'unknown',
                       'approvedForRender': listing_ok})
    missing = ['description', 'specifications', 'variants', 'gallery', 'real_usage_evidence']
    if not images:
        missing.append('primary_image')
    if not any(image['approvedForRender'] for image in images):
        missing.append('approved_media')
    for key in ('price', 'sales', 'rating'):
        if facts[key] is None:
            missing.append(key)
    context = {
        'identity': {'item_id': item_id, 'shop_id': current.get('shop_id'),
                     'name': name, 'shop_name': value('shop_name'), 'product_url': product_url},
        'facts': facts,
        'provenance': {'provider': 'addlive' if evidence_id is not None else 'catalog',
                       'evidence_record_id': evidence_id, 'retrieved_at': retrieved_at,
                       'data_source': raw.get('_source') or current.get('source_type'),
                       'provider_last_update': raw.get('lastUpdate'),
                       'provider_timezone': 'Asia/Ho_Chi_Minh', 'status': raw.get('_status'),
                       'stale': bool(raw.get('_stale')),
                       'warning': str(raw['_warning'])[:1000] if raw.get('_warning') else None,
                       'catalog_updated_at': current.get('updated_at')},
        'image_references': images, 'missing_info': missing,
        'constraints': ['Không suy ra tính năng từ tên sản phẩm.',
                        'Ảnh tham khảo không đồng nghĩa được phép dùng trong video.',
                        'Chỉ dùng trải nghiệm do chủ sở hữu cung cấp, không giả nhận đã sử dụng.'],
    }
    if experience:
        context['experience'] = {'text': str(experience)[:4000], 'source': 'owner_supplied',
                                 'verified_by_system': False}
    return context
