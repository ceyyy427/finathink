"""Narrow HTTP boundary for read-only comparisons and explicit local replay."""
import re
import sqlite3

from .reproduction import ReproductionService
from .research_differences import compare_research

BASE = '/api/research/reproduction'


def route(app, method, path, query, body):
    if path != BASE and not path.startswith(BASE + '/'):
        return None
    suffix = path[len(BASE):]
    write = suffix in {'/export', '/restore'} or suffix.endswith('/recompute')
    if method != ('POST' if write else 'GET'):
        return 405, 'application/json', {'error': '请选择此操作对应的读取或提交方式。'}
    if write:
        denied = app._data_write_guard(body)
        if denied is not None:
            return denied
    try:
        service = ReproductionService(app.research_service)
        values = {key: value for key, value in body.items() if key != '_csrf'} if write else {}
        if suffix == '':
            return 200, 'application/json', {'packages': service.list()}
        if suffix == '/compare':
            if set(query) != {'left', 'right'} or any(len(value) != 1 for value in query.values()):
                raise ValueError()
            left = app.research_service.runs.get_run(query['left'][0])
            right = app.research_service.runs.get_run(query['right'][0])
            if any(item['report_status'] != 'AVAILABLE' for item in (left, right)):
                raise ValueError()
            def side(entry):
                source = entry['manifest']['source_snapshot']
                return {'run_id': entry['run_id'], 'data_mode': source.get('data_mode', 'UNKNOWN'),
                        'quant_evidence': source.get('quant_evidence'),
                        'factor_surfaces': source.get('factor_study', {}).get('surfaces', []),
                        'report_url': '/research/' + entry['run_id']}
            return 200, 'application/json', {'comparison': compare_research(left['manifest']['source_snapshot'], right['manifest']['source_snapshot']), 'left': side(left), 'right': side(right)}
        if suffix == '/export':
            if not {'run_id'} <= values.keys() or set(values) - {'run_id', 'include_data'}:
                raise ValueError()
            return 200, 'application/json', service.export(**values)
        if suffix == '/restore':
            if set(values) != {'package'}:
                raise ValueError()
            return 201, 'application/json', service.restore(values['package'])
        matched = re.fullmatch('/([a-f0-9]{64})(/recompute)?', suffix)
        if matched:
            if values:
                raise ValueError()
            return 200, 'application/json', service.recompute(matched[1]) if write else service.get(matched[1])
        return 404, 'application/json', {'error': '研究包入口不存在，请回到研究对比页。'}
    except KeyError:
        return 404, 'application/json', {'error': '当前工作区没有这项研究或研究包，请重新选择。'}
    except (ValueError, TypeError, RecursionError):
        return 422, 'application/json', {'error': '研究包或比较条件未通过校验，请检查 JSON、原报告完整性和冻结执行身份后重试。'}
    except (OSError, sqlite3.Error):
        return 503, 'application/json', {'error': '本地研究记录暂时不可读取，请检查磁盘并重新读取。'}
