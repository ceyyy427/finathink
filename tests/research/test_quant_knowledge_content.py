"""Publication gates use authored expectations, never the evaluated result as truth."""
import copy
import math
from collections import Counter

import pytest

from finahinking.p7_5.knowledge import DEFAULT_CATALOG
from finahinking.p8_2b.catalog import DEFAULT_KNOWLEDGE_CATALOG
from finahinking.research.asset_catalog import ResearchAssetCatalog
from finahinking.research.knowledge_examples import evaluate_example
from finahinking.research.knowledge_index import KnowledgeIndex


def bundle():
    return ResearchAssetCatalog.packaged().knowledge_content()


def production_index():
    assets = ResearchAssetCatalog.packaged()
    return KnowledgeIndex.from_catalogs(DEFAULT_CATALOG, DEFAULT_KNOWLEDGE_CATALOG, assets, assets.knowledge_content())


def test_content_targets_count_only_reviewed_canonical_items():
    content = bundle()
    items = content['items']
    assert len(items) == len({i['canonical_id'] for i in items}) >= 80
    assert len(Counter(i['domain'] for i in items)) == 8
    assert len(content['paths']) >= 12
    assert len(content['examples']) >= 40
    assert len(content['misconceptions']) >= 24
    for item in items:
        assert item['review_status'] == 'REVIEWED'
        assert all(item[k] for k in ('assumptions', 'required_fields', 'source_refs', 'counterexample_refs', 'formula_refs'))
        assert any('\u4e00' <= c <= '\u9fff' for c in item['title'])
        assert item['evidence_refs'] == []
        if item['claim_kind'] == 'EMPIRICAL':
            assert item['evidence_level'] == 'SOURCE_REPORTED'
    with production_index() as index:
        assert len(index.search('', limit=100)) == 80
        assert len(index.paths()) >= 16
        for old in DEFAULT_CATALOG.concepts:
            assert index.resolve(old.concept_id)['canonical_id'] == old.concept_id


def test_examples_have_independent_expected_values():
    for example in bundle()['examples']:
        assert example['derivation'] and example['source_refs'] and example['item_id']
        assert len(example['derivation']) >= 12
        assert evaluate_example(example) == pytest.approx(example['expected'], rel=1e-10, abs=1e-10), example['example_id']


def test_calculator_rejects_code_nonfinite_and_degenerate_inputs():
    example = {'operation': 'ratio', 'inputs': {'numerator': 1, 'denominator': 0}}
    with pytest.raises(ValueError):
        evaluate_example(example)
    for operation, inputs in [('exec', {'code': 'raise SystemExit'}), ('mean', {'values': []}), ('mean', {'values': [1, math.nan]}), ('mean', {'values': [True, 1]}), ('correlation', {'x': [1, 1], 'y': [1, 2]})]:
        with pytest.raises(ValueError):
            evaluate_example({'operation': operation, 'inputs': inputs})


def test_public_nested_records_reject_unknown_private_fields():
    for field in ('formulas', 'misconceptions', 'examples', 'paths'):
        content = copy.deepcopy(bundle())
        content[field][0]['private_note'] = 'never-public'
        with pytest.raises(ValueError):
            KnowledgeIndex.from_catalogs(DEFAULT_CATALOG, DEFAULT_KNOWLEDGE_CATALOG, ResearchAssetCatalog.packaged(), content)
