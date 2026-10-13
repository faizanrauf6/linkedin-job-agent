import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from agent.models import ApplyResult, Job, Resume, Search, Verification
from agent.store import Store
from agent.worker import Worker


class RetainedBrowser:
    def __init__(self):
        self.pending_application = None

    async def verify(self, *_):
        return Verification(state='eligible', evidence=['offline fixture'])

    async def discover(self, *args, **kwargs):
        self.pending_application = None
        return []

    async def apply(self, job, profile, resume, config, before_submit, can_act):
        if self.pending_application is None or self.pending_application.job_id != job.id:
            return ApplyResult(state='needs_input', reason='Retained browser answers were lost')
        return ApplyResult(state='submitted' if before_submit() else 'needs_input')


def configured_store(tmp_path):
    store = Store(tmp_path / 'agent.db')
    config = store.config()
    config.settings.paused, config.settings.mode = False, 'automatic'
    config.profile.first_name, config.profile.email = 'Ada', 'ada@example.com'
    config.searches = [Search(id='search', min_score=0)]
    config = store.save_config(config)
    path = tmp_path / 'cv.pdf'; path.write_bytes(b'original CV')
    cv = Resume(label='CV', filename='cv.pdf', path=str(path), text='Python engineer', confirmed=True,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    store.save_resume(cv)
    return store, config, cv


@pytest.mark.asyncio
async def test_restart_processing_prioritizes_queued_retained_application_before_discovery(tmp_path, monkeypatch):
    store, config, cv = configured_store(tmp_path)
    older = store.add_job(Job(linkedin_id='older', title='Other Python engineer', company='Other', status='queued', search_id='search'))
    current = store.add_job(Job(linkedin_id='current', title='Python engineer', company='Acme', status='queued',
        search_id='search', resume_id=cv.id, resume_hash=cv.sha256, approved_revision=config.revision))
    browser = RetainedBrowser()
    browser.pending_application = SimpleNamespace(job_id=current.id)
    worker = Worker(store, browser)
    async def end_iteration(_):
        raise asyncio.CancelledError()
    monkeypatch.setattr('agent.worker.asyncio.sleep', end_iteration)
    with pytest.raises(asyncio.CancelledError):
        await worker.loop()
    assert store.job(current.id).status == 'submitted'
    assert store.job(older.id).status == 'queued'


@pytest.mark.asyncio
async def test_pause_or_cv_edit_does_not_requeue_application_waiting_for_manual_answers(tmp_path):
    store, config, cv = configured_store(tmp_path)
    job = store.add_job(Job(linkedin_id='1', title='Python engineer', company='Acme', status='needs_input',
        search_id='search', questions=['Favorite color'], error='Required answers or fields need attention', attempts=1,
        resume_id=cv.id, resume_hash=cv.sha256, revision=config.revision))
    config.settings.paused = True
    store.save_config(config)
    await Worker(store, RetainedBrowser()).reevaluate()
    refreshed = store.job(job.id)
    assert refreshed.status == 'needs_input'
    assert refreshed.questions == ['Favorite color']
    assert refreshed.error == 'Required answers or fields need attention'


def test_skipped_portal_job_cannot_be_resumed_or_claimed_after_status_change(tmp_path):
    store, config, cv = configured_store(tmp_path)
    job = store.add_job(Job(linkedin_id='1', title='Engineer', company='Acme', status='needs_input',
        application_url='https://careers.example/apply', last_error_class='PortalIntervention'))
    store.job_action(job.id, 'skip')
    changed = store.job(job.id)
    changed.status = 'needs_input'
    store.save_job(changed)
    store.resume_portal('careers.example')
    assert store.job(job.id).status != 'queued'
    changed.status = 'queued'
    store.save_job(changed)
    assert store.claim() is None


@pytest.mark.asyncio
async def test_prepared_unattempted_application_stays_held_until_explicit_queue(tmp_path):
    store, config, cv = configured_store(tmp_path)
    job = store.add_job(Job(linkedin_id='prepared', title='Python engineer', company='Acme', status='awaiting_review',
        search_id='search', resume_id=cv.id, resume_hash=cv.sha256, revision=config.revision))
    store.hold_application(job.id)
    config.settings.paused = True
    store.save_config(config)
    await Worker(store, RetainedBrowser()).reevaluate()
    assert store.job(job.id).status == 'awaiting_review'
    assert store.claim() is None
    store.job_action(job.id, 'queue')
    assert store.claim().id == job.id


@pytest.mark.asyncio
async def test_queue_during_reevaluation_does_not_restore_stale_hold(tmp_path):
    store, config, cv = configured_store(tmp_path)
    job = store.add_job(Job(linkedin_id='1', title='Python engineer', company='Acme', status='needs_input',
        attempts=1, questions=['Favorite color'], resume_id=cv.id, resume_hash=cv.sha256))
    browser = RetainedBrowser()
    async def verify(*_):
        store.job_action(job.id, 'queue')
        return Verification(state='eligible', evidence=['offline fixture'])
    browser.verify = verify
    await Worker(store, browser).reevaluate()
    assert store.job(job.id).status == 'queued'
    assert not store.application_held(job.id)
    assert store.claim().id == job.id
