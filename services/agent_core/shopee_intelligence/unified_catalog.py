"""Non-destructive compatibility bridge; reconciliation never creates observations."""
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

from .. import catalog as legacy_catalog
from ..db import connect
from .catalog import init_intelligence_schema
from .scoring import score_product

_MAP = {'product_name': 'name', 'current_price': 'price', 'total_sold': 'sold',
        'shop_name': 'shop_name', 'commission_total_rate': 'commission_rate',
        'commission_amount': 'commission', 'product_url': 'product_link',
        'image_url': 'image_url', 'affiliate_link': 'affiliate_link'}


def _identity(row):
    url = urlparse(str(row.get('product_url') or row.get('product_link') or ''))
    match = re.search(r'(?:/product/|i\.)(\d+)[/.](\d+)(?:/|$)', url.path)
    item = str(row.get('item_id') or (match.group(2) if match else '')).strip()
    shop = str(row.get('shop_id') or (match.group(1) if match else '')).strip()
    if not item.isdigit() or not shop.isdigit() or url.scheme != 'https' or url.hostname != 'shopee.vn':
        return None
    if match and (item != match.group(2) or shop != match.group(1)):
        return None
    return shop, item


def _present(value):
    return value is not None and value != ''


def _change(conn, table, key, identity, old, fields):
    changed = {k: v for k, v in fields.items() if old.get(k) != v}
    if not changed:
        return 0
    conn.execute(f"UPDATE {table} SET {','.join(k + '=?' for k in changed)} WHERE {key}=?",
                 [*changed.values(), identity])
    return 1


def _canonical_rows(conn):
    # Preserve all historical/custom IDs. Pick the most recently updated row per identity.
    selected = {}
    for row in conn.execute('SELECT * FROM shopee_products ORDER BY updated_at DESC,product_id'):
        row = dict(row)
        ident = _identity(row)
        if ident and ident not in selected:
            selected[ident] = row
    return selected


def _reconcile(conn, only_product_id=None):
    result = {k: 0 for k in ('canonical_added', 'canonical_updated', 'legacy_added', 'legacy_updated', 'conflicts', 'skipped')}
    canonical = _canonical_rows(conn)
    hidden = {r[0] for r in conn.execute('SELECT item_id FROM shopee_catalog_hidden')}
    legacy = {row['id']: dict(row) for row in conn.execute("SELECT * FROM products WHERE platform='shopee'")}
    # Fill canonical gaps from owner catalog values; never replace factual evidence.
    for old in legacy.values():
        ident = _identity(old)
        if ident is None:
            result['skipped'] += 1
            continue
        current = canonical.get(ident)
        if only_product_id is not None and (not current or current['product_id'] != only_product_id):
            continue
        if current:
            fields = {c: old.get(legacy_field) for c, legacy_field in _MAP.items()
                      if not _present(current.get(c)) and _present(old.get(legacy_field))}
            if current.get('media_usage_rights') in {'owned', 'licensed', 'permission', 'official'}:
                fields.pop('image_url', None)
            result['canonical_updated'] += _change(conn, 'shopee_products', 'product_id', current['product_id'], current, fields)
            current.update(fields)
        else:
            shop, item = ident
            # A custom ID may already equal an unrelated product's item ID.
            pid = item
            if conn.execute('SELECT 1 FROM shopee_products WHERE product_id=?', (pid,)).fetchone():
                pid = f'shopee:{shop}:{item}'
            stamp = old.get('updated_at') or old.get('imported_at') or ''
            current = {'product_id': pid, 'item_id': item, 'shop_id': shop,
                       **{c: old.get(legacy_field) for c, legacy_field in _MAP.items()},
                       'currency': 'VND', 'media_usage_rights': 'unknown',
                       'source_type': 'legacy_catalog', 'source_url': old.get('product_link'),
                       'observed_at': old.get('imported_at') or stamp,
                       'created_at': old.get('imported_at') or stamp, 'updated_at': stamp}
            if not current.get('product_name'):
                result['skipped'] += 1
                continue
            conn.execute(f"INSERT INTO shopee_products ({','.join(current)}) VALUES ({','.join('?' for _ in current)})", list(current.values()))
            canonical[ident] = current
            result['canonical_added'] += 1
    for ident, current in canonical.items():
        if only_product_id is not None and current['product_id'] != only_product_id:
            continue
        shop, item = ident
        if item in hidden:
            continue
        pid = 'shopee:' + item
        old = legacy.get(pid)
        if old and _identity(old) != ident:
            result['conflicts'] += 1
            continue
        facts = {legacy_field: current.get(c) for c, legacy_field in _MAP.items() if _present(current.get(c))}
        if 'commission_rate' not in facts:
            base, xtra = current.get('base_commission_rate'), current.get('xtra_commission_rate')
            if base is not None and xtra is not None:
                facts['commission_rate'] = base + xtra
        facts['price_text'] = str(current['current_price']) if current.get('current_price') is not None else (old or {}).get('price_text')
        if old:
            if old.get('affiliate_link'):
                if current.get('affiliate_link') and old['affiliate_link'] != current['affiliate_link']:
                    result['conflicts'] += 1
                facts['affiliate_link'] = old['affiliate_link']
            facts['updated_at'] = current.get('updated_at') or old['updated_at']
            result['legacy_updated'] += _change(conn, 'products', 'id', pid, old, facts)
        else:
            stamp = current.get('updated_at') or current.get('created_at') or ''
            values = {'id': pid, 'platform': 'shopee', 'item_id': item,
                      'name': current['product_name'], **facts,
                      'imported_at': current.get('created_at') or stamp, 'updated_at': stamp}
            conn.execute(f"INSERT INTO products ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", list(values.values()))
            result['legacy_added'] += 1
    return result


def _ready(db_path):
    init_intelligence_schema(db_path)
    legacy_catalog.ensure_schema(db_path)
    with connect(db_path) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS shopee_catalog_hidden (item_id TEXT PRIMARY KEY)')


def hide_product(db_path, catalog_id):
    """Owner action: hide from automation while retaining canonical evidence/history."""
    _ready(db_path)
    with connect(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT platform,item_id FROM products WHERE id=?', (catalog_id,)).fetchone()
        if row and row['platform'] == 'shopee':
            conn.execute('INSERT OR IGNORE INTO shopee_catalog_hidden VALUES (?)', (row['item_id'],))
        conn.execute('DELETE FROM products WHERE id=?', (catalog_id,))


def hidden_ids(db_path):
    _ready(db_path)
    with connect(db_path) as conn:
        return {r[0] for r in conn.execute('SELECT item_id FROM shopee_catalog_hidden')}


def unhide_product(db_path, item_id):
    _ready(db_path)
    with connect(db_path) as conn:
        conn.execute('DELETE FROM shopee_catalog_hidden WHERE item_id=?', (str(item_id),))


def populate_owner_links(db_path, affiliate_id, sub_id=''):
    """Fill missing owner links only; this is metadata, not a factual observation."""
    if not affiliate_id:
        return 0
    if not re.fullmatch(r'[0-9]{1,30}', str(affiliate_id)):
        raise ValueError('Affiliate ID phải là số')
    _ready(db_path)
    count = 0
    with connect(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        _reconcile(conn)  # Preserve links previously supplied through the owner catalog.
        for current in _canonical_rows(conn).values():
            if current.get('affiliate_link'):
                continue
            link = 'https://s.shopee.vn/an_redir?' + urlencode({
                'origin_link': current['product_url'], 'affiliate_id': affiliate_id, 'sub_id': sub_id})
            conn.execute('UPDATE shopee_products SET affiliate_link=? WHERE product_id=?',
                         (link, current['product_id']))
            count += 1
        _reconcile(conn)
    return count


def reconcile(settings):
    """Atomically backfill both representations, retaining identities and owner state."""
    _ready(settings.database_path)
    with connect(settings.database_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        return _reconcile(conn)


def mirror_product(db_path, product_id):
    """Mirror one already saved canonical record without changing observation history."""
    _ready(db_path)
    with connect(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        return _reconcile(conn, str(product_id))


def list_unified(settings, q='', sort='score', status=''):
    """One display row per platform/shop/item; does not perform reconciliation writes."""
    _ready(settings.database_path)
    with connect(settings.database_path) as conn:
        canonical = _canonical_rows(conn)
        products = [dict(r) for r in conn.execute('SELECT * FROM products')]
        hidden = {r[0] for r in conn.execute('SELECT item_id FROM shopee_catalog_hidden')}
        observations = {}
        for row in conn.execute('SELECT * FROM (SELECT *,ROW_NUMBER() OVER '
                                '(PARTITION BY product_id ORDER BY observed_at DESC,id DESC) AS rn '
                                'FROM shopee_product_observations) WHERE rn<=10 ORDER BY observed_at DESC,id DESC'):
            bucket = observations.setdefault(row['product_id'], [])
            if len(bucket) < 10:
                bucket.append(dict(row))
    represented = {_identity(row) for row in products if row['platform'] == 'shopee'}
    for ident, current in canonical.items():
        if ident not in represented:
            products.append({'id': 'shopee:' + ident[1], 'platform': 'shopee', 'item_id': ident[1],
                             'shop_id': ident[0],
                             'product_link': current.get('product_url') or current.get('product_link') or f'https://shopee.vn/product/{ident[0]}/{ident[1]}',
                             'product_url': current.get('product_url') or f'https://shopee.vn/product/{ident[0]}/{ident[1]}',
                             'name': current['product_name'], 'status': 'NEW',
                             'affiliate_link': current.get('affiliate_link'), 'tiktok_link': '',
                             'updated_at': current.get('updated_at')})
    result = []
    seen = set()
    for old in products:
        if old['platform'] == 'shopee' and str(old['item_id']) in hidden:
            continue
        ident = _identity(old) if old['platform'] == 'shopee' else None
        key = (old['platform'], *(ident or ('', str(old['item_id']))))
        if key in seen:
            continue
        seen.add(key)
        current = canonical.get(ident) if ident else None
        row = {**old, 'intel': current or {}, 'win_score': None, 'daily_sales_velocity': None,
               'score_status': 'PROVISIONAL', 'commission_total_rate': old.get('commission_rate'),
               'source_type': 'legacy_catalog', 'freshness': 'unknown',
               'media_usage_rights': 'unknown'}
        if current:
            for c, legacy_field in _MAP.items():
                if current.get(c) is not None and c != 'affiliate_link':
                    row[legacy_field] = current[c]
            row.update(score_product(current, observations.get(current['product_id'], [])))
            row.update(source_type=current.get('source_type'), observed_at=current.get('observed_at'),
                       media_usage_rights=current.get('media_usage_rights'),
                       canonical_product_id=current['product_id'])
            total = current.get('commission_total_rate')
            if total is None and current.get('base_commission_rate') is not None and current.get('xtra_commission_rate') is not None:
                total = current['base_commission_rate'] + current['xtra_commission_rate']
            row['commission_total_rate'] = total if total is not None else old.get('commission_rate')
            try:
                dt = datetime.fromisoformat(current['updated_at'].replace('Z', '+00:00'))
                dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                row['freshness'] = 'cached' if current.get('source_type') == 'addlive_db' else 'stale' if hours > 36 else 'recent'
            except (ValueError, TypeError):
                pass
        row['link_status'] = 'ready' if row.get('affiliate_link') or row.get('tiktok_link') else 'missing'
        row['owner_link_status'] = row['link_status']
        row['freshness_status'] = row['freshness']
        row['last_synced_at'] = (current or {}).get('updated_at') or old.get('updated_at')
        try:
            stamp = datetime.fromisoformat((row['last_synced_at'] or '').replace('Z', '+00:00'))
            stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
            row['last_synced_local'] = stamp.astimezone(ZoneInfo(getattr(settings, 'timezone', 'Asia/Ho_Chi_Minh'))).strftime('%d/%m/%Y %H:%M')
        except (TypeError, ValueError):
            row['last_synced_local'] = None
        row['is_manual'] = row['source_type'] in {'manual', 'legacy_catalog', 'dashboard_csv', 'affiliate_bulk_csv'}
        if q.lower() not in ' '.join(str(row.get(k) or '') for k in ('name', 'shop_name', 'item_id')).lower():
            continue
        if status and row.get('status') != status:
            continue
        result.append(row)
    field = {'score': 'win_score', 'win': 'win_score', 'sales': 'daily_sales_velocity', 'sold': 'sold',
             'commission': 'commission', 'rate': 'commission_rate', 'recent': 'updated_at', 'new': 'updated_at'}.get(sort, 'win_score')
    result.sort(key=lambda r: r.get(field) or ('' if field == 'updated_at' else -1), reverse=True)
    return result
