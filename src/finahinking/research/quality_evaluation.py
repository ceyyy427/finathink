"""Read-only quality measurements over frozen inputs, reports and call receipts.

Citation validity here means membership in supplied evidence, never truth of a
claim or investment performance. Unknown observations have no numerical grade.
"""
from __future__ import annotations

import math
import re

from .application_service import _model_input_digest, _read_json
from .context_bundle import build_context_bundle, normalize_materials
from .contracts import stable_digest
from .provider_config import ProviderConfig
from .quality_cases import ROLES, quality_case


def _rate(numerator, denominator):
    return numerator / denominator if denominator else None


def score_quality(job, contexts, expected_roles):
    source = job.get('model_research') or {}
    calls = source.get('calls', [])
    dispatched = [call for call in calls if call.get('dispatched') is True]
    unknown = sum(call.get('status') in {'UNKNOWN', 'PENDING'} for call in dispatched)
    accepted = sum(call.get('status') == 'READY' for call in dispatched)
    roles = source.get('roles', [])
    references = [(item.get('role'), ref) for item in roles for ref in item.get('evidence_refs', [])]
    synthesis = source.get('synthesis') or {}
    references += [('research', ref) for ref in synthesis.get('evidence_refs', [])]
    valid = sum(role in contexts and ref in contexts[role].evidence_index for role, ref in references)
    expected = [role for role, status in expected_roles.items() if status == 'INSUFFICIENT_DATA']
    observed = {item.get('role'): item for item in roles}
    present = sum(role in observed for role in expected)
    correct = sum(observed.get(role, {}).get('status') == 'INSUFFICIENT_DATA' and not observed.get(role, {}).get('evidence_refs') for role in expected)
    known_units = [call['usage']['total_units'] for call in dispatched
                   if isinstance(call.get('usage'), dict) and type(call['usage'].get('total_units')) is int and call['usage']['total_units'] >= 0]
    durations = [call['duration_ms'] for call in dispatched if type(call.get('duration_ms')) is int and call['duration_ms'] >= 0]
    return {'schema_acceptance': {'attempted': len(dispatched), 'accepted': accepted, 'unknown': unknown,
                                  'not_sent': sum(call.get('dispatched') is not True for call in calls),
                                  'rate': None if unknown else _rate(accepted, len(dispatched))},
            'citations': {'valid': valid, 'observed': len(references), 'rate': _rate(valid, len(references))},
            'insufficient_data': {'correct': correct, 'observed': present, 'expected': len(expected),
                                  'rate': _rate(correct, len(expected)) if present == len(expected) else None},
            'usage': {'known_total_units': sum(known_units), 'unknown_calls': len(dispatched)-len(known_units),
                      'total_units': sum(known_units) if dispatched and len(known_units) == len(dispatched) and not unknown else None},
            'model_duration_ms': sum(durations) if dispatched and len(durations) == len(dispatched) and not unknown else None,
            'estimated_cost': None, 'disagreements': list(synthesis.get('disagreements', []))}


class QualityEvaluation:
    def __init__(self, service, store):
        self.service, self.store = service, store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS quality_bindings (scope_id TEXT NOT NULL,job_id TEXT NOT NULL,case_id TEXT NOT NULL,case_digest TEXT NOT NULL,request_digest TEXT NOT NULL,PRIMARY KEY(scope_id,job_id))')

    @staticmethod
    def _values(values):
        if not isinstance(values, dict) or set(values)-{'case_id', 'idempotency_key', 'model_config_ref', 'allow_model_calls', 'budget'} or values.get('allow_model_calls') is not True:
            raise ValueError('请选择固定任务并明确授权本次模型调用。')
        case = quality_case(values.get('case_id'))
        key, provider = values.get('idempotency_key'), values.get('model_config_ref')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', key) or not isinstance(provider, str) or provider == 'offline':
            raise ValueError('请核对任务标识与模型配置。')
        raw = values.get('budget', {})
        if not isinstance(raw, dict) or set(raw)-{'max_wall_seconds'}:
            raise ValueError('质量任务仅接受总时长预算。')
        wall = raw.get('max_wall_seconds', 120)
        if type(wall) not in (int, float) or not math.isfinite(wall) or not 5 <= wall <= 300:
            raise ValueError('总时长应为5至300秒。')
        return case, key, provider, wall

    def _inputs(self, request, case):
        spec = request.research_plan.validation_spec
        if (request.snapshot_ref != case['snapshot_ref'] or request.instrument != case['instrument'] or request.as_of.isoformat() != case['as_of']
                or request.research_plan.hypotheses != (case['question'],) or request.analyst_roles != ROLES or spec.get('execution_mode') != 'provider-grounded'):
            raise ValueError('固定任务输入与原研究不一致。')
        reference = spec.get('context_input_ref')
        if not isinstance(reference, str) or not re.fullmatch(r'[0-9a-f]{64}', reference):
            raise ValueError('固定任务缺少冻结上下文。')
        inputs = _read_json(self.service.root/'model-inputs'/(reference+'.json'))
        if (_model_input_digest(inputs) != reference or inputs.get('scope_id') != self.store.scope_id
                or inputs.get('materials') != list(normalize_materials(case['materials'], request))):
            raise ValueError('固定任务上下文校验失败。')
        config = ProviderConfig.from_mapping(inputs['provider'])
        if config.provider_id != spec.get('model_config_ref'):
            raise ValueError('固定模型配置绑定无效。')
        return inputs, config

    def _bind(self, job_id, case):
        request = self.service._request(self.service.queue.get(job_id))
        self._inputs(request, case)
        binding = (self.store.scope_id, job_id, case['case_id'], case['case_digest'], stable_digest(request))
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO quality_bindings VALUES (?,?,?,?,?)', binding)
            row = db.execute('SELECT * FROM quality_bindings WHERE scope_id=? AND job_id=?', binding[:2]).fetchone()
            if tuple(row) != binding:
                raise ValueError('固定任务绑定已存在且输入不同。')

    def submit(self, values):
        case, key, provider, wall = self._values(values)
        if self.store.scope_id != self.service.scope_id:
            raise ValueError('评估与研究空间不一致。')
        # A lost response or lost side-table row recovers the queue-owned job;
        # it never creates another paid run or replaces its frozen provider.
        queue_key = 'application:'+stable_digest({'scope': self.service.scope_id, 'key': key})[:40]
        with self.service.queue._connect() as db:
            row = db.execute('SELECT job_id FROM jobs WHERE idempotency_key=?', (queue_key,)).fetchone()
        if row:
            request = self.service._request(self.service.queue.get(row['job_id']))
            if request.research_plan.validation_spec.get('model_config_ref') != provider or request.research_plan.validation_spec.get('budget', {}).get('max_wall_seconds') != wall:
                raise ValueError('同一任务标识不能更换模型或预算。')
            self._bind(row['job_id'], case)
            return self.service.get_job(row['job_id'])
        config = self.service.provider_configs.get(provider)
        if not self.service.allow_model_calls or config is None or not config.enabled or config.adapter_kind == 'offline' or not config.endpoint or not config.credential_ref:
            raise ValueError('请先配置模型并授权调用。')
        self.service.snapshots.save(case['snapshot'])
        job = self.service.submit({'idempotency_key': key, 'snapshot_ref': case['snapshot_ref'], 'question': case['question'],
                                   'instrument': case['instrument'], 'as_of': case['as_of'], 'materials': case['materials'],
                                   'model_config_ref': provider, 'allow_model_calls': True,
                                   'budget': {'max_wall_seconds': wall, 'max_attempts': 1, 'max_provider_calls': 6}})
        self._bind(job['job_id'], case)
        return job

    def evaluate(self, job_id):
        with self.store.connect() as db:
            binding = db.execute('SELECT * FROM quality_bindings WHERE scope_id=? AND job_id=?', (self.store.scope_id, job_id)).fetchone()
        if binding is None:
            raise KeyError('quality task unavailable')
        record = self.service.queue.get(job_id)
        request = self.service._request(record)
        case = quality_case(binding['case_id'])
        if binding['case_digest'] != case['case_digest'] or binding['request_digest'] != stable_digest(request):
            raise ValueError('固定任务版本或研究绑定校验失败。')
        inputs, config = self._inputs(request, case)
        snapshot = self.service.snapshots.load(request.snapshot_ref)
        job = self.service.get_job(job_id)
        state = self.service._progress(record, request)
        reports = state.analyst_reports if state is not None else ()
        contexts = {role: build_context_bundle(request, snapshot, role, materials=inputs['materials']) for role in ROLES}
        contexts['research'] = build_context_bundle(request, snapshot, 'research', materials=inputs['materials'], reports=reports)
        source = job.get('model_research') or {}
        for call in source.get('calls', []):
            role = call.get('role')
            if role not in contexts or call.get('model') != config.model_for_role(role) or call.get('context_fingerprint') != contexts[role].fingerprint:
                raise ValueError('模型调用与固定任务上下文不一致。')
        invalid = job.get('failure_kind') == 'PUBLICATION_INVALID'
        metrics = score_quality({} if invalid else job, contexts, case['expected_roles'])
        status = 'UNAVAILABLE' if invalid else 'EVALUATED' if job['status'] == 'completed' else 'PARTIAL' if source.get('calls') else 'NOT_STARTED'
        spec = request.research_plan.validation_spec
        conditions = {'case_digest': case['case_digest'], 'snapshot_ref': request.snapshot_ref, 'policy_fingerprint': spec['policy_fingerprint'],
                      'policy_ref': request.previous_policy_fingerprint, 'workflow_version': request.workflow_version, 'budget': spec['budget']}
        return {'schema_version': 'research-quality.v1', 'job_id': job_id, 'run_id': request.run_id, 'case_id': case['case_id'], 'case_label': case['label'],
                'data_mode': 'SAMPLE', 'status': status, 'job_status': job['status'], 'conditions': conditions, 'comparison_key': stable_digest(conditions),
                'provider_id': config.provider_id, 'role_models': {role: config.model_for_role(role) for role in (*ROLES, 'research')},
                'metrics': metrics, 'roles': list(source.get('roles', [])) if not invalid else [],
                'evidence_links': [job['report_url']+'#model-research', job['report_url']+'#source'] if job['report_status'] == 'AVAILABLE' and not invalid else [],
                'run_url': job['run_url'], 'limitations': ['引用有效率仅检查输入证据归属；不评价事实真伪或投资收益。', '质量结果仅适用于本次固定样例、角色模型映射与冻结条件。']}

    def list(self, *, limit=20):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('质量任务分页无效。')
        with self.store.connect() as db:
            rows = db.execute('SELECT job_id,case_id FROM quality_bindings WHERE scope_id=? ORDER BY rowid DESC LIMIT ?', (self.store.scope_id, limit)).fetchall()
        return [{'job_id': row['job_id'], 'case_id': row['case_id']} for row in rows]

    def compare(self, job_ids):
        if not isinstance(job_ids, list) or not 2 <= len(job_ids) <= 4 or any(not isinstance(job, str) for job in job_ids) or len(set(job_ids)) != len(job_ids):
            raise ValueError('请选择2至4个不同的固定质量任务。')
        rows = [self.evaluate(job) for job in job_ids]
        same = len({row['comparison_key'] for row in rows}) == 1
        return {'schema_version': 'research-quality-comparison.v1', 'comparable': same, 'items': rows,
                'reason': 'SAME_FROZEN_CONDITIONS' if same else 'DIFFERENT_FROZEN_CONDITIONS'}
