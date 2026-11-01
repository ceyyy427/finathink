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


def test_fractional_tail_and_invalid_covariance_have_independent_checks():
    # At 85%, 10 observations give 1.5 tail mass: (0.10 + .5*.09)/1.5.
    result = evaluate_example({'operation': 'fractional_es', 'inputs': {'returns': [-.01, -.02, -.03, -.04, -.05, -.06, -.07, -.08, -.09, -.10], 'confidence': .85}})
    assert result == pytest.approx(.09666666666666668)
    for matrix in ([[1, 2], [0, 1]], [[1, 2], [2, 1]]):
        with pytest.raises(ValueError):
            evaluate_example({'operation': 'portfolio_variance', 'inputs': {'weights': [.5, .5], 'covariance': matrix}})
    for confidence in (0, 1):
        with pytest.raises(ValueError):
            evaluate_example({'operation': 'nearest_rank_var', 'inputs': {'returns': [.01, -.02], 'confidence': confidence}})


def test_learning_paths_put_included_prerequisites_before_dependents():
    content = bundle()
    items = {i['canonical_id']: i for i in content['items']}
    for path in content['paths']:
        positions = {key: n for n, key in enumerate(path['item_ids'])}
        for key, position in positions.items():
            assert all(parent not in positions or positions[parent] < position for parent in items[key]['prerequisites']), (path['path_id'], key)
