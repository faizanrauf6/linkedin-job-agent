import io

import pytest
from docx import Document
from fastapi.testclient import TestClient

from agent.api import create_app
from agent.models import Job, Resume


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, run_worker=False)) as client:
        yield client


def upload(client, email):
    document = Document()
    document.add_paragraph('Name: Jhon Example\nEmail: ' + email + '\nReact software engineer with four years of experience.')
    buffer = io.BytesIO()
    document.save(buffer)
    return client.post('/api/resumes', files={'file': ('cv.docx', buffer.getvalue())}).json()


def test_conflicting_cv_cannot_be_confirmed_without_acknowledgement(client):
    config = client.get('/api/config').json()
    config['profile']['email'] = 'jhon@example.com'
    assert client.put('/api/config', json=config).status_code == 200
    cv = upload(client, 'other@example.com')
    assert cv['conflicts']
    assert client.patch('/api/resumes/' + cv['id'], json={'confirmed': True}).status_code == 409
    assert client.patch('/api/resumes/' + cv['id'], json={'confirmed': True, 'conflict_acknowledged': True, 'conflict_fingerprint': cv['conflict_fingerprint']}).status_code == 200
    config = client.get('/api/config').json()
    config['profile']['email'] = 'changed@example.com'
    client.put('/api/config', json=config)
    refreshed = client.get('/api/resumes').json()[0]
    assert not refreshed['confirmed']
    assert not refreshed['conflict_acknowledged']


def test_stale_cv_conflict_acknowledgement_cannot_confirm_new_evidence(client):
    config = client.get('/api/config').json()
    config['profile']['email'] = 'jhon@example.com'
    client.put('/api/config', json=config)
    cv = upload(client, 'other@example.com')
    stale = {'confirmed': True, 'conflict_acknowledged': True, 'conflict_fingerprint': cv['conflict_fingerprint']}
    config = client.get('/api/config').json()
    config['profile']['email'] = 'new@example.com'
    client.put('/api/config', json=config)
    assert client.patch('/api/resumes/' + cv['id'], json=stale).status_code == 409
    assert not client.get('/api/resumes').json()[0]['confirmed']


def test_legacy_confirmed_cv_is_reassessed_before_agent_startup(tmp_path):
    from agent.store import Store
    store = Store(tmp_path / 'agent.db')
    config = store.config()
    config.profile.email = 'jhon@example.com'
    store.save_config(config)
    store.save_resume(Resume(label='Legacy CV', text='Name: Jhon Example\nEmail: other@example.com\nReact developer', confirmed=True))
    with TestClient(create_app(tmp_path, run_worker=False)) as client:
        cv = client.get('/api/resumes').json()[0]
        assert cv['conflicts']
        assert not cv['confirmed']


def test_submission_guard_rechecks_cv_identity_against_current_profile(tmp_path):
    import hashlib
    from agent.store import Store
    store = Store(tmp_path / 'agent.db')
    config = store.config()
    config.settings.paused, config.settings.mode = False, 'automatic'
    config.profile.email = 'jhon@example.com'
    config = store.save_config(config)
    path = tmp_path / 'cv.pdf'
    path.write_bytes(b'fixture')
    resume = Resume(label='CV', text='Email: other@example.com\nReact developer', confirmed=True,
                    path=str(path), sha256=hashlib.sha256(b'fixture').hexdigest())
    store.save_resume(resume)
    job = store.add_job(Job(linkedin_id='1', title='Engineer', company='Example', status='queued', resume_id=resume.id, resume_hash=resume.sha256))
    store.claim()
    assert not store.begin_submission(job.id, config.revision)


def test_validation_reports_unperformed_live_checks(client):
    response = client.get('/api/validation')
    assert response.status_code == 200
    report = response.json()
    assert report['session'] is None
    assert report['preparation'] is None
    assert not report['setup']['profile']


def test_session_check_records_result_without_starting_worker(client, monkeypatch):
    async def check():
        return {'state': 'connected', 'details': 'Signed-in jobs page visible'}
    monkeypatch.setattr(client.app.state.browser, 'check_session', check)
    result = client.post('/api/validation/session')
    assert result.status_code == 200
    assert client.get('/api/validation').json()['session']['state'] == 'connected'
    assert client.get('/api/status').json()['paused']


def test_preparation_never_reserves_submission(client, monkeypatch):
    from agent.models import ApplyResult
    store = client.app.state.store
    config = store.config()
    config.profile.first_name, config.profile.email = 'Jhon', 'jhon@example.com'
    store.save_config(config)
    resume = Resume(label='CV', confirmed=True)
    store.save_resume(resume)
    job = store.add_job(Job(linkedin_id='1', title='Engineer', company='Example', status='awaiting_review'))
    async def evaluate(job, config):
        job.resume_id = resume.id
        return resume
    async def apply(job, profile, resume, config, before_submit, can_act, dry_run=False):
        assert dry_run
        assert before_submit() is False
        return ApplyResult(state='needs_input', reason='Ready for review')
    monkeypatch.setattr(client.app.state.worker, 'evaluate', evaluate)
    monkeypatch.setattr(client.app.state.browser, 'apply', apply)
    response = client.post('/api/jobs/' + job.id + '/prepare')
    assert response.status_code == 200
    assert not store.job(job.id).submission_started
    assert store.job(job.id).status == 'awaiting_review'
    assert client.get('/api/validation').json()['preparation']['job_id'] == job.id


def test_preparation_rejects_previously_submitted_job(client):
    job = client.app.state.store.add_job(Job(linkedin_id='1', title='Engineer', company='Example', status='submitted', submission_started=True))
    assert client.post('/api/jobs/' + job.id + '/prepare').status_code == 409


def test_preparation_does_not_overwrite_a_newer_skip(client, monkeypatch):
    from agent.models import ApplyResult
    store = client.app.state.store
    config = store.config()
    config.profile.first_name, config.profile.email = 'Ada', 'ada@example.com'
    store.save_config(config)
    cv = Resume(label='CV', confirmed=True)
    store.save_resume(cv)
    job = store.add_job(Job(linkedin_id='1', title='Engineer', company='Example', status='awaiting_review'))
    async def evaluate(*_):
        return cv
    async def apply(*_, **kwargs):
        store.job_action(job.id, 'skip')
        return ApplyResult(state='needs_input', reason='Ready for review')
    monkeypatch.setattr(client.app.state.worker, 'evaluate', evaluate)
    monkeypatch.setattr(client.app.state.browser, 'apply', apply)
    response = client.post('/api/jobs/' + job.id + '/prepare')
    assert response.status_code == 409
    assert store.job(job.id).status == 'rejected'
    assert store.manually_skipped(job.id)


def test_portals_and_startup_are_inspectable_without_side_effects(client):
    assert client.get('/api/portals').status_code == 200
    response = client.get('/api/startup')
    assert response.status_code == 200
    assert 'supported' in response.json()
    assert not response.json()['enabled']


def test_invalid_startup_configuration_returns_useful_error(client, monkeypatch):
    def configure(*args):
        raise ValueError('Startup belongs to another checkout')
    monkeypatch.setattr('agent.operations.configure_startup', configure)
    response = client.post('/api/startup', json={'enabled': True})
    assert response.status_code == 400


def test_live_checks_require_pause(client):
    config = client.app.state.store.config()
    config.settings.paused = False
    client.app.state.store.save_config(config)
    assert client.post('/api/validation/session').status_code == 409
