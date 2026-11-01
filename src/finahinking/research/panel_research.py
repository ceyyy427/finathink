"""Explicit local panel evaluation shared by HTTP and registered worker stages."""
from __future__ import annotations

import math

from finahinking.data.panel_contracts import PanelContract
from finahinking.data.research_fields import _json
from finahinking.factors.dsl import evaluate_panel_expression
from finahinking.factors.panel_operators import SEMANTICS_VERSION, parse_panel_expression

from .contracts import stable_digest


def evaluate_panel_request(snapshot_store, request):
    if not isinstance(request, dict) or set(request) != {'contract', 'attachment_refs', 'expressions'}:
        raise ValueError('面板研究需要合同、附件引用和表达式')
    if len(_json(request).encode()) > 32768:
        raise ValueError('面板请求上下文超过 32768 字节')
    expressions = request['expressions']
    if not isinstance(expressions, list) or not 1 <= len(expressions) <= 24:
        raise ValueError('每轮面板研究需要 1 到 24 个表达式')
    contract = PanelContract.from_dict(request['contract'])
    panel = snapshot_store.build_panel(contract, request['attachment_refs'])
    parsed = [parse_panel_expression(e, panel.frame.columns, row_count=len(panel.frame)) for e in expressions]
    if sum(p.cost for p in parsed) > 5_000_000:
        raise ValueError('本轮面板研究计算预算超限，请拆分任务')
    if len(panel.frame) * len(expressions) > 50000:
        raise ValueError('面板研究输出预算超限，请缩小范围')
    results = []
    for item in parsed:
        series = evaluate_panel_expression(item.expression, panel)
        records = [{'observed_at': t.isoformat(), 'instrument': a, 'value': float(v) if math.isfinite(v) else None}
                   for (t, a), v in series.items()]
        results.append({'expression': item.expression, 'metadata': series.attrs, 'records': records})
    payload = {'schema_version': 'panel-research.v1', 'semantics_version': SEMANTICS_VERSION,
               'panel_fingerprint': panel.fingerprint, 'contract': contract.to_dict(), 'audit': panel.audit,
               'status': panel.status, 'results': results, 'production_write': False,
               'execution_permission': False}
    if len(_json(payload).encode()) > 4 * 1024 * 1024:
        raise ValueError('面板结果超过导出预算，请缩小范围')
    return {**payload, 'fingerprint': stable_digest(payload)}
