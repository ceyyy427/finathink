"""Versioned synthetic tasks; catalogue reads never create data or call a model."""
from __future__ import annotations

from dataclasses import replace

from finahinking.p8_2.data_sources import FixtureMarketDataSource

from .contracts import stable_digest

VERSION = 'research-quality-cases.v1'
ROLES = ('fundamentals', 'technical', 'sentiment', 'news', 'learning')
_LABELS = {'complete': '材料齐全', 'sparse': '材料不足', 'conflict': '观点分歧'}
_SUMMARIES = {
    'fundamentals': 'Synthetic company: steady revenue, rising costs; no audited financial statements supplied.',
    'sentiment': 'Synthetic survey: cautiously optimistic; small sample with selection bias.',
    'news': 'Synthetic announcement: planned product launch, timing remains uncertain.',
    'learning': 'Synthetic settled paper review: earlier signal was unstable; check independent periods.',
}


def quality_case(case_id):
    if type(case_id) is not str or case_id not in _LABELS:
        raise ValueError('请选择目录中的固定质量任务。')
    snapshot = FixtureMarketDataSource(source_id='quality-synthetic-v1', points=24).snapshot('SYNTH')
    snapshot = replace(snapshot, observations=tuple(replace(row, suspended=False, industry='SYNTHETIC') for row in snapshot.observations),
                       provenance={**snapshot.provenance, 'price_unit': 'USD', 'volume_unit': 'shares'})
    summaries = {} if case_id == 'sparse' else dict(_SUMMARIES)
    if case_id == 'conflict':
        summaries.update(fundamentals='Synthetic company summary: demand is improving; management expects growth.',
                         news='Synthetic independent news summary: demand is weakening and orders were delayed. This conflicts with the company summary.')
    materials = [{'role': role, 'instrument': 'SYNTH', 'summary': summary, 'source_label': 'Synthetic quality case '+role,
                  'observed_at': '2026-09-30T10:00:00+00:00', 'available_at': '2026-09-30T12:00:00+00:00', 'authorized': True}
                 for role, summary in sorted(summaries.items())]
    case = {'case_id': case_id, 'version': VERSION, 'label': _LABELS[case_id], 'data_mode': 'SAMPLE', 'instrument': 'SYNTH', 'as_of': '2026-10-01',
            'question': 'Review this synthetic quality task: '+case_id+'. Cite supplied evidence, state missing inputs and preserve disagreements; propose checks only.',
            'snapshot_ref': snapshot.fingerprint, 'materials': materials,
            'expected_roles': {role: 'INSUFFICIENT_DATA' if case_id == 'sparse' and role != 'technical' else 'READY' for role in ROLES}}
    return {**case, 'case_digest': stable_digest(case), 'snapshot': snapshot}


def quality_cases():
    return [{key: value for key, value in quality_case(case_id).items() if key != 'snapshot'} for case_id in _LABELS]
