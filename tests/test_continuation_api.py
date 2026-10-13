import pytest
from fastapi.testclient import TestClient

from agent.api import create_app
from agent.models import Job, Resume


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    app = create_app(tmp_path, run_worker=False)
    cv = Resume(label='Selected', confirmed=True, sha256='verified')
    app.state.store.save_resume(cv)
    job = app.state.store.add_job(Job(linkedin_id='1', title='Engineer', company='Acme',
        status='needs_input', resume_id=cv.id, resume_hash=cv.sha256))
    async def snapshot(*_):
        return {'token':'snapshot', 'fields':[{'key':'field', 'label':'Favorite color', 'value':'blue'}]}
    async def confirm(job, config, resume, token):
        if token != 'snapshot':
            raise ValueError('The browser answers changed')
        return await snapshot()
    monkeypatch.setattr(app.state.browser, 'get_continuation', snapshot, raising=False)
    monkeypatch.setattr(app.state.browser, 'confirm_continuation', confirm, raising=False)
    with TestClient(app) as client:
        yield client, job


def test_current_browser_answers_require_explicit_confirmation_before_queue(workspace):
    client, job = workspace
    url = '/api/jobs/' + job.id + '/continuation'
    response = client.get(url)
    assert response.status_code == 200
    assert response.json()['fields'][0]['value'] == 'blue'
    assert client.post(url, json={'token':'snapshot', 'confirmation':False}).status_code == 409
    assert client.app.state.store.job(job.id).status == 'needs_input'
    assert client.post(url, json={'token':'stale', 'confirmation':True}).status_code == 409
    assert client.app.state.store.job(job.id).status == 'needs_input'
    assert client.post(url, json={'token':'snapshot', 'confirmation':True}).status_code == 200
    assert client.app.state.store.job(job.id).status == 'queued'
    assert not client.app.state.store.job(job.id).submission_started


def test_continuation_rejects_running_or_uncertain_application(workspace):
    client, job = workspace
    url = '/api/jobs/' + job.id + '/continuation'
    config = client.app.state.store.config()
    config.settings.paused = False
    client.app.state.store.save_config(config)
    assert client.get(url).status_code == 409
    config = client.app.state.store.config()
    config.settings.paused = True
    client.app.state.store.save_config(config)
    job.status, job.submission_started = 'submission_uncertain', True
    client.app.state.store.save_job(job)
    assert client.post(url, json={'token':'snapshot', 'confirmation':True}).status_code == 409


@pytest.mark.parametrize('change', ['skip', 'cv'])
def test_continuation_rejects_job_or_cv_change_during_browser_confirmation(workspace, monkeypatch, change):
    client, job = workspace
    store = client.app.state.store
    async def confirm(*_):
        if change == 'skip':
            store.job_action(job.id, 'skip')
        else:
            cv = store.resumes()[0]
            cv.confirmed = False
            store.save_resume(cv)
    monkeypatch.setattr(client.app.state.browser, 'confirm_continuation', confirm)
    response = client.post('/api/jobs/' + job.id + '/continuation', json={'token':'snapshot', 'confirmation':True})
    assert response.status_code == 409
    assert store.job(job.id).status != 'queued'
    if change == 'skip':
        assert store.manually_skipped(job.id)
