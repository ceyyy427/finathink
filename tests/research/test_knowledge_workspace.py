"""Public lookup and preparation must not mutate private research state."""
import json

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication


@pytest.fixture
def app():
    app = LocalApplication(LocalAppConfig(db_path=':memory:', offline=True))
    yield app
    app.close()


def test_read_only_lookup_has_no_job_or_provider_effects(app, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('private or provider path was invoked')
    monkeypatch.setattr(app, 'personal', forbidden)
    before = app.connection.total_changes
    for url in ('/knowledge', '/api/knowledge/search?q=波动', '/api/knowledge/items/volatility', '/api/knowledge/paths', '/api/knowledge/export', '/knowledge/items/point-in-time', '/knowledge/prepare?item=point-in-time'):
        status, _, payload = app.route('GET', url)
        assert status == 200, (url, payload)
    assert app._research_application is None
    assert app.connection.total_changes == before
    assert app.route('POST', '/api/knowledge/search')[0] == 405
    assert app.route('POST', '/knowledge/prepare?item=return')[0] == 405


def test_search_to_preparation_keeps_source_and_needs_data(app):
    status, _, result = app.route('GET', '/api/knowledge/search?q=PIT')
    assert status == 200
    assert result['items'][0]['canonical_id'] == 'point-in-time'
    detail = app.route('GET', '/api/knowledge/items/point-in-time')[2]
    page = app.route('GET', '/knowledge/prepare?item=point-in-time')[2]
    for text in ('NEEDS_DATA', 'available_at', 'factor-mining-workflow', detail['catalog_fingerprint'], detail['source_refs'][0]['url']):
        assert text in page
    assert '创建研究任务' not in page
    assert app._research_application is None


def test_unknown_bad_filters_and_html_are_safe(app):
    assert app.route('GET', '/api/knowledge/items/not-a-concept')[0] == 404
    assert app.route('GET', '/api/knowledge/search?domain=invalid')[0] == 400
    assert app.route('GET', '/api/knowledge/search?q=' + 'a' * 201)[0] == 400
    page = app.route('GET', '/knowledge?q=%3Cscript%3Eevil%3C/script%3E')[2]
    assert '<script>evil' not in page
    exported = app.route('GET', '/api/knowledge/export')[2]
    assert 'session_id' not in json.dumps(exported)


def test_real_private_learning_survives_public_cache_rebuild(tmp_path):
    from dataclasses import asdict

    from finahinking.p7 import MasteryEvidence, PersonalNode
    from finahinking.p7_5.knowledge import DEFAULT_CATALOG
    from finahinking.p8_2b.catalog import DEFAULT_KNOWLEDGE_CATALOG

    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / 'private.sqlite3'), offline=True))
    try:
        session = app.config.session_id
        app.repository.save_node(session, PersonalNode('volatility', 'concept', 'private sentinel', {'note': 'private-learning-sentinel'}))
        app.repository.save_mastery_evidence(session, MasteryEvidence('private-check', 'volatility', 'quiz_response', 'knowledge:volatility:q1', 'correct', '2026-10-31T12:00:00Z'))
        before = asdict(app.repository.get_mastery_state(session, 'volatility'))
        for concept in DEFAULT_CATALOG.concepts:
            assert app.route('GET', '/knowledge/' + concept.concept_id)[0] == 200
        for old in DEFAULT_KNOWLEDGE_CATALOG.canonical_ids():
            assert app.route('GET', '/knowledge/' + old)[0] == 200
        for path in DEFAULT_CATALOG.learning_paths:
            assert app.route('GET', '/api/knowledge/path/' + path.path_id)[0] == 200
        exported = app.route('GET', '/api/knowledge/export')[2]
        app._quant_knowledge.close()
        app._quant_knowledge = None
        cache = tmp_path / '.knowledge-cache/public.sqlite3'
        assert cache.exists()
        cache.unlink()
        assert app.route('GET', '/api/knowledge/export')[2] == exported
        assert 'private-learning-sentinel' not in json.dumps(exported)
        assert asdict(app.repository.get_mastery_state(session, 'volatility')) == before
        assert app.repository.get_node(session, 'volatility').payload['note'] == 'private-learning-sentinel'
        assert app._research_application is None
    finally:
        app.close()


def test_foreign_cache_is_a_recoverable_service_error_not_a_bad_query(tmp_path):
    import sqlite3

    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / 'private.sqlite3'), offline=True))
    cache = tmp_path / '.knowledge-cache/public.sqlite3'
    cache.parent.mkdir()
    try:
        with sqlite3.connect(cache) as db:
            db.execute('CREATE TABLE private_notes (note TEXT)')
            db.execute("INSERT INTO private_notes VALUES ('private-sentinel')")
        before = cache.read_bytes()
        status, _, payload = app.route('GET', '/api/knowledge/search?q=收益')
        assert status == 503
        assert '缓存' in payload['error'] and '检索条件无效' not in payload['error']
        assert cache.read_bytes() == before
    finally:
        app.close()


def test_injected_memory_database_never_uses_configured_disk_cache(tmp_path):
    import sqlite3

    cache = tmp_path / '.knowledge-cache/public.sqlite3'
    cache.parent.mkdir()
    with sqlite3.connect(cache) as db:
        db.execute('CREATE TABLE unrelated (note TEXT)')
        db.execute("INSERT INTO unrelated VALUES ('leave this file alone')")
    before = cache.read_bytes()
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / 'configured.sqlite3')), connection=sqlite3.connect(':memory:'))
    try:
        assert app.route('GET', '/api/knowledge/search?q=回撤')[0] == 200
        assert cache.read_bytes() == before
    finally:
        app.close()


def test_injected_file_database_places_cache_beside_actual_main_database(tmp_path):
    import sqlite3

    actual = tmp_path / 'actual'
    actual.mkdir()
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / 'configured.sqlite3')), connection=sqlite3.connect(actual / 'private.sqlite3'))
    try:
        assert app.route('GET', '/api/knowledge/search?q=回撤')[0] == 200
        assert (actual / '.knowledge-cache/public.sqlite3').exists()
        assert not (tmp_path / '.knowledge-cache').exists()
    finally:
        app.close()
