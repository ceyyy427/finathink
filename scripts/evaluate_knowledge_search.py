"""Evaluate frozen relevance sets without query-specific ranking rules."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from finahinking.p7_5.knowledge import DEFAULT_CATALOG
from finahinking.p8_2b.catalog import DEFAULT_KNOWLEDGE_CATALOG
from finahinking.research.asset_catalog import ResearchAssetCatalog
from finahinking.research.knowledge_index import KnowledgeIndex

GOLDEN_SHA = '5a4cef5f7f9d4bbcfa7a950a8a59545958ab1360d7884bc7352a885da8260a0b'


def evaluate(split='all'):
    path = Path(__file__).resolve().parents[1] / 'tests/fixtures/quant_knowledge/search-goldens.json'
    if hashlib.sha256(path.read_bytes()).hexdigest() != GOLDEN_SHA:
        raise ValueError('frozen relevance set changed')
    rows = [r for r in json.loads(path.read_text())['queries'] if split == 'all' or r['split'] == split]
    assets = ResearchAssetCatalog.packaged()
    outcomes, domains = [], defaultdict(list)
    with KnowledgeIndex.from_catalogs(DEFAULT_CATALOG, DEFAULT_KNOWLEDGE_CATALOG, assets, assets.knowledge_content()) as index:
        for row in rows:
            found = [r['canonical_id'] for r in index.search(row['query'], limit=5)]
            hit = bool(set(found) & set(row['relevant_ids']))
            domains[row['domain']].append(hit)
            outcomes.append({'query_id': row['query_id'], 'hit': hit, 'top5': found})
        rates = {d: {'hits': sum(v), 'count': len(v), 'rate': sum(v)/len(v)} for d, v in sorted(domains.items())}
        total = sum(r['hit'] for r in outcomes) / len(rows)
        return {'split': split, 'fingerprint': index.fingerprint, 'golden_sha256': GOLDEN_SHA,
                'top5_rate': total, 'domains': rates, 'pass': total >= .85 and all(v['rate'] >= .75 for v in rates.values()), 'outcomes': outcomes}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=('development', 'holdout', 'all'), default='development')
    result = evaluate(parser.parse_args().split)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['pass'] else 1)
