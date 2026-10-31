"""Phase 20 local HTTP boundary: reads never enqueue; writes require CSRF."""
import re
import sqlite3

from .contracts import _unsafe_public_text
from .job_maintenance import JobMaintenance
from .learning_assessments import LearningAssessmentService
from .learning_replays import LearningReplayService

BASE = '/api/research/learning'


def route(app, method, path, query, body):
    assessment = BASE + '/assessments'
    pair = BASE + '/pairs'
    if not (path in {'/api/research/maintenance', '/api/research/jobs/summaries', assessment, pair} or path.startswith((assessment+'/', pair+'/'))):
        return None
    if method not in {'GET', 'POST'}:
        return 405, 'application/json', {'error': '请使用读取或显式提交操作。'}
    if method == 'POST':
        denied = app._data_write_guard(body)
        if denied is not None:
            return denied
    values = {key: value for key, value in body.items() if key != '_csrf'} if method == 'POST' else {}
    try:
        service = app.research_service
        if path == '/api/research/jobs/summaries' and method == 'GET':
            if set(query)-{'limit', 'cursor'} or any(len(value) != 1 for value in query.values()):
                raise ValueError()
            return 200, 'application/json', JobMaintenance(service).summaries(limit=int(query.get('limit',['20'])[0]), cursor=query.get('cursor',[None])[0])
        if path == '/api/research/maintenance' and method == 'GET':
            return 200, 'application/json', JobMaintenance(service).status()
        if path == assessment:
            assessments = LearningAssessmentService(service)
            if method == 'GET':
                fields = ('assessment_id', 'status', 'sample_count', 'as_of', 'start', 'anchor_review_id')
                return 200, 'application/json', {'assessments': [{key: item[key] for key in fields} for item in assessments.list()]}
            if set(values) != {'anchor_review_id', 'start', 'as_of'}:
                raise ValueError()
            return 201, 'application/json', assessments.assess(values)
        if path == pair:
            pairs = LearningReplayService(service)
            if method == 'GET':
                return 200, 'application/json', {'pairs': pairs.list()}
            if set(values) != {'review_id', 'snapshot_ref', 'as_of'}:
                raise ValueError()
            return 201, 'application/json', pairs.create(values)
        match = re.fullmatch(re.escape(BASE)+r'/(assessments|pairs)/((?:assessment|pair)-[a-f0-9]{64})(/(?:propose|resume))?', path)
        if match:
            kind, identity, action = match.groups()
            if bool(action) != (method == 'POST') or values:
                return 405, 'application/json', {'error': '请显式提交建议或继续复跑；读取不会创建任务。'}
            if kind == 'assessments' and action in (None, '/propose'):
                target = LearningAssessmentService(service)
                return 200, 'application/json', target.propose(identity) if action else target.get(identity)
            if kind == 'pairs' and action in (None, '/resume'):
                target = LearningReplayService(service)
                return 200, 'application/json', target.resume(identity) if action else target.get(identity)
        return 405, 'application/json', {'error': '该入口不支持此操作，请返回复盘或维护页。'}
    except KeyError:
        return 404, 'application/json', {'error': '当前工作区未找到所选记录，请重新读取列表。'}
    except (ValueError, TypeError, OverflowError) as exc:
        message = str(exc)
        safe = bool(re.search('[\u4e00-\u9fff]', message)) and len(message) <= 240 and not _unsafe_public_text(message)
        return 422, 'application/json', {'error': message if safe else '条件未通过校验，请核对日期、证据完整性、批准状态与分页范围后重试。'}
    except (OSError, sqlite3.Error):
        return 503, 'application/json', {'error': '本地记录暂不可用，请检查磁盘后重新读取；未完成的成对任务可显式继续。'}
