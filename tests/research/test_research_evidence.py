"""Evidence is a checked projection, never a new approval flag."""
import copy
import importlib.util

import pytest

from finahinking.research.contracts import stable_digest


def api():
    assert importlib.util.find_spec('finahinking.research.research_evidence'), 'checked evidence projection is not implemented'
    from finahinking.research.research_evidence import build_research_evidence
    return build_research_evidence


class Catalogue:
    def __init__(self, status='PASS', mode='SAMPLE'):
        self.records = {}
        refs = []
        for i in range(20):
            row = {'record_id':f'counterexample:c{i}', 'kind':'COUNTEREXAMPLE', 'status':'REPRODUCED',
                   'case_id':f'c{i}', 'data_mode':'SAMPLE', 'observed':{'gate':'checked'}, 'input':{'i':i}}
            row['fingerprint'] = stable_digest(row)
            self.records[row['record_id']] = row
            refs.append({'record_id':row['record_id'], 'digest':row['fingerprint']})
        row = {'record_id':'run:one', 'kind':'FACTOR_TRIAL', 'status':status, 'data_mode':mode,
               'factor_id':'factor-a', 'formula_fingerprint':'a'*64,'snapshot_ref':'b'*64,
               'policy_fingerprint':'c'*64,'counterevidence_refs':refs,'metrics':{'mean_ic':.1}}
        row['fingerprint'] = stable_digest(row)
        self.records[row['record_id']] = row
        self.refs = [{'record_id':'run:one','digest':row['fingerprint']}]
    def load_record(self, record_id):
        return copy.deepcopy(self.records[record_id])


def test_projection_checks_existing_facts_without_granting_use():
    cat = Catalogue()
    value = api()(cat.refs, catalogue=cat)
    assert value['status'] == 'COMPLETE'
    assert value['records'][0]['evidence_level'] == 'SYNTHETIC_REPRODUCED'
    assert value['use_approved'] is False and value['production_write'] is False
    assert value['learning_eligible'] is False
    assert value == api()(cat.refs, catalogue=cat)


@pytest.mark.parametrize('status', ['NEEDS_DATA','EXECUTION_FAILED','CANCELLED'])
def test_execution_failure_is_not_financial_falsification(status):
    cat = Catalogue(status)
    value = api()(cat.refs, catalogue=cat)
    assert value['records'][0]['financial_falsification'] is False
    assert value['learning_eligible'] is False
    assert value['records'][0]['evidence_level'] == 'DEFINITION_CHECKED'


def test_negative_research_can_have_complete_synthetic_evidence():
    cat = Catalogue('FALSIFIED')
    value = api()(cat.refs, catalogue=cat)
    assert value['records'][0]['financial_falsification'] is False
    assert value['records'][0]['hypothesis_outcome'] == 'FALSIFIED'
    assert value['records'][0]['evidence_level'] == 'SYNTHETIC_REPRODUCED'
    assert value['learning_eligible'] is False


@pytest.mark.parametrize('corruption', ['hash','missing','omitted','status'])
def test_corrupt_or_missing_counterevidence_blocks_upgrade(corruption):
    cat = Catalogue()
    if corruption == 'hash': cat.records['counterexample:c0']['observed'] = {'gate':'altered'}
    elif corruption == 'missing': del cat.records['counterexample:c0']
    else:
        row = cat.records['run:one']
        if corruption == 'omitted': row['counterevidence_refs'].pop()
        else: row['counterevidence_refs'][0]['digest'] = 'd'*64
        row['fingerprint'] = stable_digest({k:v for k,v in row.items() if k!='fingerprint'})
        cat.refs[0]['digest'] = row['fingerprint']
    value = api()(cat.refs, catalogue=cat)
    assert value['status'] == 'BLOCKED'
    assert value['records'][0]['evidence_level'] == 'DEFINITION_CHECKED'
    assert value['learning_eligible'] is False


def test_phase20_learning_gate_does_not_treat_suite_evidence_as_settlement():
    from finahinking.research.learning_reliability import assess_outcomes
    from tests.research.test_learning_reliability import SCOPE, rows
    data = rows()
    data[0]['research_evidence'] = api()(Catalogue().refs, catalogue=Catalogue())
    result = assess_outcomes(data, scope=SCOPE, start='2025-01-01', as_of='2026-01-02')
    assert result['status'] == 'OBSERVE'
    assert result['sample_count'] == 19
    assert result['exclusion_counts']['RESEARCH_NOT_SETTLED'] == 1
    assert not result['factor_weight_updates']


@pytest.mark.parametrize('mode', ['CAPTURED', 'USER_DATA', 'UNKNOWN'])
def test_falsification_level_respects_verified_data_mode(mode):
    cat = Catalogue('FALSIFIED',mode)
    value = api()(cat.refs,catalogue=cat)
    row = value['records'][0]
    assert row['financial_falsification'] is (mode in {'CAPTURED','USER_DATA'})
    if mode == 'UNKNOWN': assert value['status'] == 'BLOCKED'


def test_twenty_counterexamples_remain_visible():
    import json
    from pathlib import Path

    from finahinking.research.research_evidence import PackagedCounterexamples
    class Local:
        def read_text(self, resource_id, **kwargs):
            assert resource_id == 'factor-counterexamples'
            return (Path(__file__).parents[2]/'src/finahinking/_package_data/research_assets/knowledge/counterexamples.json').read_text()
    cat = PackagedCounterexamples(Local())
    assert len(cat.refs()) == 22
    for ref in cat.refs():
        row = cat.load_record(ref['record_id'])
        assert row['status'] == 'REPRODUCED', json.dumps(row,ensure_ascii=False)
        assert row['input'] and row['observed'] and row['correction'] and row['scope']
        assert row['financial_falsification'] is False
