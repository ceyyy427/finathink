from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.model_workbench import ModelWorkbench
from finahinking.research.provider_config import ProviderConfig, ProviderConfigStore
from finahinking.research.provider_status import ProviderCredentialRef
from tests.research.test_application_service import payload
from tests.research.test_context_bundle import material


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    store = ProviderConfigStore(tmp_path / 'providers.json')
    store.save(ProviderConfig('desk', 'openai_compatible', 'gpt-4o',
                              endpoint='https://models.acme.com/v1',
                              credential_ref=ProviderCredentialRef('desk', env_var='FINAHINK_TEST_KEY'),
                              role_models={'technical': 'gpt-4.1'}))
    monkeypatch.setenv('FINAHINK_TEST_KEY', 'only-in-controlled-test')
    app = ResearchApplicationService(tmp_path / 'artifacts', provider_configs=store)
    try:
        yield ModelWorkbench(app)
    finally:
        app.close()


def approval(preview):
    return {'preview_ref': preview['preview_ref'], 'preview_digest': preview['preview_digest'], 'authorized': True}


def test_sample_preview_is_a_local_projection_and_authorization_enqueues_once(workbench):
    app = workbench.service
    settings = workbench.settings()
    sample = settings['snapshots'][0]
    assert sample['snapshot_ref'] == 'sample' and sample['instruments'] == ['SYNTH']
    assert not app.snapshots.list_snapshots() and not settings['history']
    setup = next(p for p in settings['providers'] if p['provider_id'] == 'desk')
    assert setup['role_models']['technical'] == 'gpt-4.1' and setup['credential_ready']
    assert 'only-in-controlled-test' not in str(settings) and 'models.acme.com' not in str(settings)

    preview = workbench.preview({'snapshot_ref': 'sample', 'question': '检查当期价格。', 'model_config_ref': 'offline'})
    assert preview['status'] == 'READY' and preview['expected_calls'] == 0
    assert preview['selection']['snapshot_ref'] == sample['fingerprint']
    assert not app.snapshots.list_snapshots() and app.list_jobs()['total'] == 0
    for changes in ({'authorized': False}, {'authorized': 'true'}, {'preview_digest': '0' * 64},
                    {'question': 'silently replace material'}, {'allow_model_calls': True}):
        with pytest.raises((ValueError, KeyError)):
            workbench.authorize({**approval(preview), **changes})
    assert app.list_jobs()['total'] == 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(lambda _: workbench.authorize(approval(preview))['job_id'], range(3)))
    assert len(set(ids)) == 1 and app.list_jobs()['total'] == 1
    assert workbench.evidence(ids[0])['dispatch_status'] == 'OFFLINE'
    assert workbench.settings()['history'][0]['job_id'] == ids[0]
    assert app.snapshots.load(sample['fingerprint']).mode == 'SAMPLE'


def test_snapshot_role_materials_and_provider_changes_are_bound_to_the_preview(workbench, monkeypatch):
    app = workbench.service
    values = {**payload(app, key='bound'), 'model_config_ref': 'desk', 'materials': [material()]}
    preview = workbench.preview(values)
    assert preview['status'] == 'READY' and preview['expected_calls'] == 3
    by_role = {r['role']: r for r in preview['roles']}
    assert by_role['news']['materials'][0]['summary'] == material()['summary']
    assert by_role['technical']['model'] == 'gpt-4.1'
    assert by_role['fundamentals']['material_status'] == 'INSUFFICIENT_DATA'
    assert by_role['research']['dynamic_observations'] is True
    assert by_role['news']['evidence_refs'] == [by_role['news']['materials'][0]['evidence_ref']]
    assert workbench.preview(values)['preview_digest'] == preview['preview_digest']
    monkeypatch.delenv('FINAHINK_TEST_KEY')
    assert workbench.preview(values)['blockers'][0]['code'] == 'CREDENTIAL_MISSING'
    with pytest.raises(ValueError):
        workbench.authorize(approval(preview))
    monkeypatch.setenv('FINAHINK_TEST_KEY', 'only-in-controlled-test')
    original = app.provider_configs.get('desk')
    app.provider_configs.remove('desk')
    app.provider_configs.save(replace(original, endpoint='https://updated.acme.com/v1'))
    with pytest.raises(ValueError):
        workbench.authorize(approval(preview))
    changed = workbench.preview(values)
    assert changed['preview_digest'] != preview['preview_digest']
    first = workbench.authorize(approval(changed))
    conflicting = workbench.preview({**values, 'question': '改变研究问题'})
    with pytest.raises(ValueError):
        workbench.authorize(approval(conflicting))
    assert workbench.authorize(approval(changed))['job_id'] == first['job_id']
    assert app.list_jobs()['total'] == 1


@pytest.mark.parametrize('changes', [
    {'instrument': 'OTHER'}, {'as_of': '2021-01-01'}, {'materials': [material(authorized=False)]},
    {'materials': [material(available_at='2027-01-01T00:00:00Z')]},
    {'budget': {'max_attempts': 2}}, {'budget': {'max_provider_calls': 1}},
    {'model_config_ref': 'unknown'},
])
def test_blocked_preview_gives_a_reason_and_cannot_enqueue(workbench, changes):
    values = {**payload(workbench.service), 'model_config_ref': 'desk'}
    preview = workbench.preview({**values, **changes})
    assert preview['status'] == 'BLOCKED' and preview['blockers'] and preview['preview_ref'] is None
    with pytest.raises((ValueError, KeyError)):
        workbench.authorize(approval(preview))
    assert workbench.service.list_jobs()['total'] == 0


def test_preview_is_private_to_scope_and_integrity_checked(workbench):
    app = workbench.service
    preview = workbench.preview({**payload(app), 'model_config_ref': 'desk'})
    other = ResearchApplicationService(app.root, scope_id='other', provider_configs=app.provider_configs)
    try:
        with pytest.raises((ValueError, KeyError)):
            ModelWorkbench(other).authorize(approval(preview))
        job = workbench.authorize(approval(preview))
        assert ModelWorkbench(other).settings()['history'] == []
        with pytest.raises(KeyError):
            ModelWorkbench(other).evidence(job['job_id'])
        app.cancel(job['job_id'])
        app.provider_configs.remove('desk')
        assert workbench.authorize(approval(preview))['status'] == 'cancelled'
        evidence = workbench.evidence(job['job_id'])
        assert evidence['dispatch_status'] == 'NOT_SENT' and evidence['calls'] == []
        assert evidence['usage_status'] == 'NOT_CALLED' and evidence['semantic_status'] == 'NO_EVIDENCE'
    finally:
        other.close()


def test_other_process_settings_changes_invalidate_authorization(workbench):
    app = workbench.service
    inputs = {**payload(app), "model_config_ref": "desk"}
    preview = workbench.preview(inputs)
    other = ProviderConfigStore(app.root.parent / "providers.json")
    config = other.get("desk")
    other.remove("desk")
    other.save(replace(config, role_models={"research": "a-new-manager"}))
    with pytest.raises(ValueError):
        workbench.authorize(approval(preview))
    assert app.list_jobs()["total"] == 0
    assert workbench.preview(inputs)["preview_digest"] != preview["preview_digest"]


def test_equivalent_defaults_share_one_frozen_receipt_and_never_resolve_or_send_on_read(workbench, monkeypatch):
    from finahinking.research.credentials import LocalProviderCredentialStore
    from finahinking.research.provider_transport import ProviderHttpTransport

    def unexpected(*args, **kwargs):
        pytest.fail("settings and previews cannot send or resolve a credential for use")

    monkeypatch.setattr(ProviderHttpTransport, "request", unexpected)
    monkeypatch.setattr(LocalProviderCredentialStore, "resolve", unexpected)
    app = workbench.service
    inputs = {**payload(app), "model_config_ref": "desk"}
    first = workbench.preview(inputs)
    second = workbench.preview({**inputs, "question": " " + inputs["question"] + " ", "materials": [],
                                "budget": {"max_attempts": 1, "max_provider_calls": 6}})
    assert first["preview_ref"] == second["preview_ref"]
    assert workbench.settings()["history"] == [] and not (app.root / "model-calls.sqlite3").exists()


def test_keychain_readiness_reads_entry_metadata_only(workbench, monkeypatch):
    from subprocess import CompletedProcess

    from finahinking.data.connection_settings import MacOSKeychainBackend

    calls = []

    def lookup(self, args, **kwargs):
        calls.append(args)
        assert "-w" not in args
        return CompletedProcess(args, 0, "entry metadata", "")

    app = workbench.service
    old = app.provider_configs.get("desk")
    app.provider_configs.remove("desk")
    app.provider_configs.save(replace(old, credential_ref=ProviderCredentialRef("desk", keychain_label="finathink-workbench")))
    monkeypatch.setattr(MacOSKeychainBackend, "_run", lookup)
    assert next(p for p in workbench.settings()["providers"] if p["provider_id"] == "desk")["credential_ready"]
    assert workbench.preview({**payload(app), "model_config_ref": "desk"})["status"] == "READY"
    assert len(calls) == 2 and all(call[0] == "find-generic-password" for call in calls)
