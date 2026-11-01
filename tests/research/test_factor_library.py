import importlib.util


def api():
    assert importlib.util.find_spec('finahinking.research.factor_library'), 'Phase24 library contract is missing'
    from finahinking.research.factor_library import formula_fingerprint
    return formula_fingerprint


def test_alias_and_window_grid_do_not_inflate_formula_count():
    fingerprint = api()
    ids = {fingerprint(f'ts_mean(close,{n},{n})', 'CNY', 'ts') for n in range(2,22)}
    assert len(ids) == 1
    assert fingerprint('negate(ts_mean(close,5,5))','CNY','ts') in ids
    assert fingerprint('multiply(close,2)','CNY','ts') != fingerprint('multiply(close,3)','CNY','ts')


def test_legacy_spec_refs_and_approval_scopes_survive():
    from finahinking.research.factor_recipes import research_recipes
    # Existing trusted recipe execution is not replaced by library ingestion.
    assert len(research_recipes()) == 27

import copy
import json
from dataclasses import replace

import pandas as pd
import pytest

from finahinking.research.factor_library import (
    FactorLibrary,
    load_factor_library,
    parse_library_expression,
)
from tests.data.test_research_panel import fixture


def documents():
    card = {'card_id':'source-a','title':'来源 A','source_id':'fixture',
            'repository':'https://github.com/example/project','commit':'a'*40,
            'path':'factor.py','sha256':'b'*64,'license_id':'MIT','license_path':'LICENSE',
            'read_date':'2026-11-01','symbol':'moving_mean','original_formula':'mean(close)',
            'formula_status':'EXPLICIT','adaptation_status':'INDEPENDENT_IMPLEMENTATION',
            'evidence_status':'SOURCE_REPORTED','factor_id':'mean-a'}
    definition = {'factor_id':'mean-a','title':'均值测试','family':'trend_momentum','version':'1.0.0',
                  'formula':'ts_mean(close,3,3)','required_fields':['close'],'field_units':{'close':'CNY'},
                  'units':'CNY','window':3,'knowledge_refs':[],'source_card_ids':['source-a'],
                  'numeric_example':{'input':[1,2,3],'expected':2},'counterexample':'缺少价格时不可计算',
                  'direction_scope':'研究变量，方向依赖验证','scope':'ts'}
    return {'schema_version':'factor-library-sources.v1','cards':[card]}, {'schema_version':'factor-library-formulas.v1','formulas':[definition]}


class Catalog:
    def __init__(self, cards=None, formulas=None):
        source, entries = documents()
        self.data = {'factor-library-sources':cards or source, 'factor-library-formulas':formulas or entries}
    def read_text(self, name, **kwargs):
        return json.dumps(self.data[name])


def test_source_only_and_multi_source_alias_do_not_grant_extra_definitions():
    cards, formulas = documents()
    cards['cards'].append({**cards['cards'][0],'card_id':'source-only','formula_status':'UNRESOLVED','factor_id':None})
    cards['cards'].append({**cards['cards'][0],'card_id':'source-b'})
    formulas['formulas'].append({**formulas['formulas'][0],'factor_id':'mean-b','formula':'ts_mean(close,5,5)','source_card_ids':['source-b']})
    catalog = Catalog(cards,formulas); library = FactorLibrary(catalog)
    assert library.inventory()['source_card_count'] == 3
    assert library.inventory()['executable_formula_count'] == 1
    assert library.inventory()['alias_count'] == 1
    assert len(load_factor_library(catalog)) == 1
    assert library.spec('mean-a').implementation_fingerprint != library.spec('mean-b').implementation_fingerprint
    assert library.inventory()['production_write'] is False
    with pytest.raises(ValueError): library.spec('source-only')
    exposed = library.entries()[0]; exposed['source_card_ids'].append('tampered')
    assert 'tampered' not in library.entries()[0]['source_card_ids']


@pytest.mark.parametrize('expression', ['__import__("os")', 'close.__class__', 'close[-1]', 'ts_lag(close,-1)',
    'ts_mean(close,3,4)', 'add(close,float("inf"))', '[x for x in close]', 'add(close,True)'])
def test_unsafe_or_forward_expressions_rejected(expression):
    with pytest.raises(ValueError): parse_library_expression(expression,['close'])


def test_direction_alias_does_not_erase_economic_subtraction():
    fp=api()
    assert fp('add(close,negate(open))','CNY','ts') != fp('add(close,open)','CNY','ts')
    assert fp('multiply(close,2)','CNY','ts') != fp('multiply(close,2)','USD','ts')
    assert fp('cs_winsorize(close,0.1,0.9,3)','CNY','cs') != fp('cs_winsorize(close,0.2,0.8,3)','CNY','cs')


def test_formula_version_fields_and_provenance_fail_closed():
    cards, formulas=documents()
    for mutation in ({'version':'2.0.0'}, {'required_fields':['future_price'],'field_units':{'future_price':'CNY'},'formula':'future_price'},
                     {'source_card_ids':['missing']}, {'field_units':{'close':'shares'}}):
        changed=copy.deepcopy(formulas); changed['formulas'][0].update(mutation)
        with pytest.raises(ValueError): FactorLibrary(Catalog(cards,changed))
    library=FactorLibrary(Catalog())
    with pytest.raises(ValueError): replace(library.spec('mean-a'),version='2.0.0')
    with pytest.raises(ValueError): replace(library.spec('mean-a'),implementation_ref='library:mean-a@2.0.0')


def test_nested_arithmetic_golden_and_input_identity():
    cards, formulas=documents(); row=formulas['formulas'][0]
    row.update(formula='divide(subtract(close,ts_mean(close,3,3)),ts_mean(close,3,3))', units='ratio')
    library=FactorLibrary(Catalog(cards,formulas)); snapshot,rules,contract,build=fixture()
    panel=build(snapshot,[rules],contract)
    result=library.evaluate('mean-a',panel)
    assert result['values'][-3]['value'] == pytest.approx(1/3)
    assert result['factor_spec']['direction']=='RESEARCH'
    assert result['unit']=='ratio'
    assert result['fingerprint']==library.evaluate('mean-a',panel)['fingerprint']
    with pytest.raises(ValueError): library.evaluate(replace(library.spec('mean-a'),formula='close'),panel)
    from finahinking.data.research_panel import ResearchPanel
    frame=panel.frame; frame.loc[frame.index.get_level_values(0).max(),'close']*=10
    altered=ResearchPanel(frame,panel.audit,contract,'f'*64)
    rerun=library.evaluate('mean-a',altered)
    assert rerun['values'][:-3]==result['values'][:-3]
    assert rerun['fingerprint']!=result['fingerprint']
    missing=ResearchPanel(panel.frame.drop(columns='close'),panel.audit,contract,panel.fingerprint)
    assert library.evaluate('mean-a',missing)['status']=='NEEDS_DATA'


def test_zero_denominator_and_units_never_become_valid_values():
    cards, formulas=documents(); formulas['formulas'][0].update(formula='divide(close,subtract(close,close))',units='ratio')
    library=FactorLibrary(Catalog(cards,formulas)); snap,rules,contract,build=fixture(); panel=build(snap,[rules],contract)
    result=library.evaluate('mean-a',panel)
    assert result['status']=='NEEDS_DATA' and 'ZERO_DENOMINATOR' in result['missing_reason']
    assert all(row['value'] is None for row in result['values'])


def test_output_unit_mismatch_and_invalid_panel_are_rejected():
    from finahinking.data.research_panel import ResearchPanel
    cards, formulas=documents(); formulas['formulas'][0]['units']='ratio'
    library=FactorLibrary(Catalog(cards,formulas)); snap,rules,contract,build=fixture(); panel=build(snap,[rules],contract)
    with pytest.raises(ValueError,match='输出单位'): library.evaluate('mean-a',panel)
    formulas['formulas'][0].update(formula='close',units='CNY')
    library=FactorLibrary(Catalog(cards,formulas))
    invalid=ResearchPanel(panel.frame.reset_index(),panel.audit,contract,panel.fingerprint)
    with pytest.raises(ValueError,match='索引'): library.evaluate('mean-a',invalid)
    duplicated=ResearchPanel(pd.concat([panel.frame,panel.frame]),panel.audit,contract,panel.fingerprint)
    with pytest.raises(ValueError,match='索引'): library.evaluate('mean-a',duplicated)


def test_log_rejects_currency_instead_of_erasing_its_dimension():
    cards, formulas = documents()
    formulas['formulas'][0].update(formula='log(close)', units='ratio')
    library = FactorLibrary(Catalog(cards, formulas))
    snapshot, rules, contract, build = fixture()
    panel = build(snapshot, [rules], contract)
    with pytest.raises(ValueError, match='对数输入'):
        library.evaluate('mean-a', panel)


@pytest.mark.parametrize('expression,field,unit,input_value,expected', [
    ('log(add(volume,1))', 'volume', 'shares', 99.0, 4.605170185988092),
    ('log(style_beta)', 'style_beta', 'ratio', 2.0, 0.6931471805599453),
])
def test_log_accepts_dimensionless_values_and_fixed_one_share_baseline(expression, field, unit, input_value, expected):
    from finahinking.data.research_panel import ResearchPanel

    cards, formulas = documents()
    formulas['formulas'][0].update(formula=expression, required_fields=[field],
                                  field_units={field:unit}, units='ratio')
    library = FactorLibrary(Catalog(cards, formulas))
    snapshot, rules, contract, build = fixture()
    original = build(snapshot, [rules], contract)
    frame, audit = original.frame, original.audit
    frame[field] = input_value
    audit['units'][field] = unit
    panel = ResearchPanel(frame, audit, contract, original.fingerprint)
    result = library.evaluate('mean-a', panel)
    assert result['unit'] == 'ratio'
    assert all(row['value'] == pytest.approx(expected) for row in result['values'])
    for wrong_unit in ('lots', 'thousand_shares'):
        audit['units'][field] = wrong_unit
        rejected = library.evaluate('mean-a', ResearchPanel(frame, audit, contract, original.fingerprint))
        assert rejected['status'] == 'NEEDS_DATA'
        assert all(row['value'] is None for row in rejected['values'])
