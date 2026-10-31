from datetime import UTC, datetime

from tests.research.test_application_service import payload, wait_for
from tests.research.test_phase8_learning_flow import inputs, settle
from tests.research.test_phase18_api import app_http, post

__all__ = ['app_http']
BASE = '/api/research/learning'


def test_inert_pages_scoped_lists_validation_and_no_hidden_calls(app_http):
    app, http, wire = app_http
    for path in ('/research/maintenance', '/research/learning', '/api/research/maintenance', '/api/research/jobs/summaries', BASE+'/assessments', BASE+'/pairs'):
        assert http(path)[0] == 200
    assert http(BASE+'/assessments')[1] == {'assessments': []}
    assert http(BASE+'/pairs')[1] == {'pairs': []}
    assert http(BASE+'/assessments', {})[0] == 403
    assert post(app, http, BASE+'/assessments', {})[0] == 422
    assert http('/api/research/jobs/summaries?limit=101')[0] == 422
    assert http('/api/research/jobs/summaries?cursor=broken')[0] == 422
    assert app.research_service.list_jobs()['total'] == 0 and not wire.calls


def test_http_assessment_exploratory_approval_pair_and_reopen(app_http, monkeypatch):
    app, http, wire = app_http
    service = app.research_service
    source, ref, evidence = inputs(service)
    review = settle(service, source, evidence)
    status, assessment = post(app, http, BASE+'/assessments', {'anchor_review_id': review['review_id'], 'start': '2026-01-01', 'as_of': datetime.now(UTC).date().isoformat()})
    assert status == 201 and assessment['status'] == 'OBSERVE'
    assert post(app, http, BASE+'/assessments/'+assessment['assessment_id']+'/propose', {})[0] == 422
    values = {'decision':'APPROVED', 'rationale':'明确作为探索性试验', 'expected_preview_fingerprint': review['preview_fingerprint']}
    assert post(app, http, BASE+'/'+review['review_id']+'/decision', values)[0] == 422
    status, approved = post(app, http, BASE+'/'+review['review_id']+'/decision', {**values, 'allow_exploratory':True})
    assert status == 200
    original_submit, calls = service.submit, []
    def partial(body, **kwargs):
        calls.append(body)
        if len(calls) == 2:
            raise OSError('simulated second enqueue shutdown')
        return original_submit(body, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(service, 'submit', partial)
        status, _ = post(app, http, BASE+'/pairs', {'review_id':review['review_id'], 'snapshot_ref':ref, 'as_of':approved['available_as_of']})
        assert status == 503
    pair = http(BASE+'/pairs')[1]['pairs'][0]
    assert pair['status'] == 'INCOMPLETE' and service.list_jobs()['total'] == 2
    assert http(BASE+'/pairs/'+pair['pair_id'])[1]['status'] == 'INCOMPLETE'
    status, pair = post(app, http, BASE+'/pairs/'+pair['pair_id']+'/resume', {})
    assert status == 200
    for child in pair['children'].values():
        assert wait_for(service, child['job_id'])['status'] == 'completed'
    assert http(BASE+'/pairs/'+pair['pair_id'])[1]['status'] == 'AVAILABLE'
    assert len(http(BASE+'/pairs')[1]['pairs']) == 1
    assert service.list_jobs()['total'] == 3 and not wire.calls


def test_summary_metadata_is_not_report_integrity(app_http):
    app, http, _ = app_http
    service = app.research_service
    for i in range(3):
        service.submit(payload(service, key=f'meta-{i}'))
    page = http('/api/research/jobs/summaries?limit=2')[1]
    assert len(page['jobs']) == 2 and page['total'] == 3
    assert page['jobs'][0]['report_status'] == 'NOT_CHECKED'
    assert len(http('/api/research/jobs/summaries?limit=2&cursor='+page['next_cursor'])[1]['jobs']) == 1
    assert http('/api/research/maintenance')[1]['counts'] == {'queued':3}
