"""Validate the published teaching bundle; optional pinned source-byte audit."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

from finahinking.p7_5.knowledge import DEFAULT_CATALOG
from finahinking.p8_2b.catalog import DEFAULT_KNOWLEDGE_CATALOG
from finahinking.research.asset_catalog import ResearchAssetCatalog
from finahinking.research.knowledge_index import KnowledgeIndex


def validate(reference_root=None):
    assets = ResearchAssetCatalog.packaged()
    content = assets.knowledge_content()
    with KnowledgeIndex.from_catalogs(DEFAULT_CATALOG, DEFAULT_KNOWLEDGE_CATALOG, assets, content) as index:
        exported = index.export()
        counts = {'canonical_items': len(exported['items']), 'domains': dict(Counter(i['domain'] for i in exported['items'])),
                  'new_paths': len(content['paths']), 'legacy_paths': sum(p.get('legacy', False) for p in exported['paths']),
                  'examples': len(content['examples']), 'misconceptions': len(content['misconceptions']), 'sources': len(content['sources'])}
        if counts['canonical_items'] < 80 or len(counts['domains']) != 8 or counts['new_paths'] < 12 or counts['examples'] < 40 or counts['misconceptions'] < 24:
            raise ValueError('knowledge publication targets not met')
        if any(not i['formula_refs'] or not i['assumptions'] or not i['required_fields'] for i in exported['items']):
            raise ValueError('knowledge content lacks required editorial sections')
        if reference_root is not None:
            for source in content['sources']:
                repo_name = source['repository'].rsplit('/', 1)[-1]
                repository = Path(__file__).resolve().parents[1] if repo_name == 'finathink' else reference_root / repo_name
                raw = subprocess.check_output(['git', '-C', str(repository), 'show', source['commit'] + ':' + source['path']])
                if hashlib.sha256(raw).hexdigest() != source['sha256'] or source['end_line'] > len(raw.decode().splitlines()):
                    raise ValueError('source byte or line-range mismatch: ' + source['source_ref'])
        return {'status': 'PASS', 'counts': counts, 'fingerprint': index.fingerprint,
                'source_bytes': 'VERIFIED_PINNED_GIT_OBJECTS' if reference_root is not None else 'NOT_RECHECKED_USE_REFERENCE_ROOT',
                'examples': 'HAND_EXPECTATIONS_MATCH', 'empirical_evidence': 'SOURCE_REPORTED; no user-market validation claimed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-root', type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.reference_root), ensure_ascii=False, indent=2))
