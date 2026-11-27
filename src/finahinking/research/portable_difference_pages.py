"""Store full field-level differences alongside the exact local receipt which owns them."""
import hashlib
import json

from .contracts import stable_digest

PAGE_SIZE = 100


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS research_difference_pages (
        scope_id TEXT NOT NULL, package_id TEXT NOT NULL, receipt_id TEXT NOT NULL,
        product TEXT NOT NULL, page INTEGER NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY (scope_id,package_id,product,page))""")


class DifferencePages:
    """Stage a comparison on a connection-private temporary table, one page at a time."""

    def __init__(self, db):
        self.db = db
        db.execute('PRAGMA temp_store=FILE')
        db.execute("""CREATE TEMP TABLE pending_differences (
            product TEXT, page INTEGER, digest TEXT, payload TEXT, PRIMARY KEY(product,page))""")
        self.product = None
        self.items = []
        self.counts = {}
        self.roots = {}

    def add(self, product, difference):
        if self.product != product:
            self._flush()
            self.product = product
        self.items.append(difference)
        if len(self.items) == PAGE_SIZE:
            self._flush()

    def _flush(self):
        if not self.items:
            return
        count = self.counts.get(self.product, 0)
        payload = json.dumps(self.items, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(payload.encode()).digest()
        self.db.execute('INSERT INTO pending_differences VALUES(?,?,?,?)',
                        (self.product, count // PAGE_SIZE, digest.hex(), payload))
        self.roots.setdefault(self.product, hashlib.sha256()).update(digest)
        self.counts[self.product] = count + len(self.items)
        self.items.clear()

    def save(self, scope, receipt):
        self._flush()
        for product, row in receipt['products'].items():
            if row['status'] in {'DIFFERENT', 'REPRODUCED'}:
                if self.counts.get(product, 0) != row['difference_count']:
                    raise ValueError('差异页数与复算记录不一致。')
                row['full_differences_available'] = True
                row['difference_pages_digest'] = self.roots.get(product, hashlib.sha256()).hexdigest()
            elif product in self.counts:
                # A failed comparison has no authoritative field-level result.
                self.db.execute('DELETE FROM pending_differences WHERE product=?', (product,))
        receipt['receipt_id'] = stable_digest(receipt)
        # The temporary writes above never lock the workspace during replay.
        self.db.commit()
        self.db.execute('BEGIN IMMEDIATE')
        binding = (scope, receipt['package_id'], receipt['receipt_id'])
        self.db.execute('DELETE FROM research_difference_pages WHERE scope_id=? AND package_id=?', binding[:2])
        self.db.execute("""INSERT INTO research_difference_pages
            SELECT ?,?,?,product,page,digest,payload FROM pending_differences""", binding)
        encoded = json.dumps(receipt, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        self.db.execute('UPDATE research_packages SET receipt=?,receipt_digest=? WHERE scope_id=? AND package_id=?',
                        (encoded, stable_digest(receipt), *binding[:2]))
        return receipt


def read_page(db, scope, reference, product, page, receipt_id=None):
    if type(page) is not int or page < 0 or page > 2**31 or not isinstance(product, str) or len(product) > 128:
        raise ValueError('差异页或产物标识无效。')
    db.execute('BEGIN')
    saved = db.execute('SELECT receipt,receipt_digest FROM research_packages WHERE scope_id=? AND package_id=?',
                       (scope, reference)).fetchone()
    if saved is None:
        raise KeyError('研究包不在当前工作区。')
    receipt = json.loads(saved['receipt']) if saved['receipt'] else None
    if not receipt or stable_digest(receipt) != saved['receipt_digest'] or receipt.get('package_id') != reference:
        raise ValueError('当前没有可校验的复算记录。')
    identity = receipt.get('receipt_id')
    if (not identity or stable_digest({k: v for k, v in receipt.items() if k != 'receipt_id'}) != identity
            or receipt_id is not None and receipt_id != identity):
        raise ValueError('差异页与当前复算记录不一致。')
    row = receipt['products'].get(product)
    if not isinstance(row, dict) or row.get('full_differences_available') is not True:
        raise ValueError('当前产物尚无完整差异记录。')
    total = row['difference_count']
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    if page >= max(1, pages):
        raise ValueError('差异页超出当前产物的范围。')
    binding = (scope, reference, identity, product)
    root = hashlib.sha256()
    count = 0
    for saved_page in db.execute("""SELECT page,digest FROM research_difference_pages
            WHERE scope_id=? AND package_id=? AND receipt_id=? AND product=? ORDER BY page""", binding):
        if saved_page['page'] != count:
            raise ValueError('差异页索引不连续。')
        root.update(bytes.fromhex(saved_page['digest']))
        count += 1
    if count != pages or root.hexdigest() != row['difference_pages_digest']:
        raise ValueError('差异页索引未通过复算记录校验。')
    items = []
    if total:
        stored = db.execute("""SELECT payload,digest FROM research_difference_pages
            WHERE scope_id=? AND package_id=? AND receipt_id=? AND product=? AND page=?""", (*binding, page)).fetchone()
        if stored is None or hashlib.sha256(stored['payload'].encode()).hexdigest() != stored['digest']:
            raise ValueError('差异页内容未通过校验。')
        items = json.loads(stored['payload'])
        if not isinstance(items, list) or len(items) != min(PAGE_SIZE, total - page * PAGE_SIZE):
            raise ValueError('差异条数未通过校验。')
    return {'package_id': reference, 'receipt_id': identity, 'product': product, 'page': page,
            'page_size': PAGE_SIZE, 'total': total, 'differences': items, 'has_more': page + 1 < pages}
