import hashlib

import pytest

from agent.models import ApplyResult, Config, Job, Resume, Search, Verification
from agent.store import Store
from agent.worker import Worker


def test_duplicate_linkedin_ids_and_external_urls_share_one_application(tmp_path):
    store = Store(tmp_path / "agent.db")
    a = store.add_job(Job(linkedin_id="1", title="Engineer", company="Acme", application_url="https://jobs.lever.co/acme/123?utm_source=linkedin"))
    b = store.add_job(Job(linkedin_id="2", title="Engineer", company="Acme", application_url="https://jobs.lever.co/acme/123?source=linkedin"))
    assert a.id == b.id
    assert len(store.jobs()) == 1


def test_restart_never_requeues_uncertain_submission(tmp_path):
    path = tmp_path / "agent.db"
    store = Store(path)
    store.add_job(Job(linkedin_id="1", title="Engineer", company="Acme", status="applying", submission_started=True))
    store.add_job(Job(linkedin_id="2", title="Backend Engineer", company="Acme", status="applying"))
    restarted = Store(path)
    restarted.recover()
    states = {j.linkedin_id: j.status for j in restarted.jobs()}
    assert states == {"1": "submission_uncertain", "2": "queued"}


def test_submission_guard_honors_pause_revision_and_cap(tmp_path):
    store = Store(tmp_path / "agent.db")
    config = store.config()
    config.settings.paused = False
    config.settings.daily_limit = 1
    config.settings.mode = "automatic"
    config = store.save_config(config)
    path = tmp_path / "cv.pdf"; path.write_bytes(b"fixture")
    cv = Resume(label="CV", path=str(path), confirmed=True, sha256=hashlib.sha256(b"fixture").hexdigest())
    store.save_resume(cv)
    a = store.add_job(Job(linkedin_id="1", title="Engineer", company="Acme", status="queued", resume_id=cv.id, resume_hash=cv.sha256))
    b = store.add_job(Job(linkedin_id="2", title="Engineer", company="Acme", status="queued"))
    a = store.claim()
    assert store.begin_submission(a.id, config.revision)
    assert not store.begin_submission(b.id, config.revision)
    config.settings.paused = True
    store.save_config(config)
    assert not store.begin_submission(a.id, config.revision)


class FixtureBrowser:
    async def verify(self, job, config):
        return Verification(state="eligible", evidence=["Fixture employer"], allowed_hosts=["example.com"])

    async def apply(self, job, profile, resume, config, before_submit, can_act=lambda: True):
        if before_submit():
            raise RuntimeError("Connection dropped after submit")
        return ApplyResult(state="needs_input", reason="Submission cancelled")


@pytest.mark.asyncio
async def test_worker_connection_drop_after_submission_is_uncertain(tmp_path):
    store = Store(tmp_path / "agent.db")
    config = store.config()
    config.settings.paused = False
    config.settings.mode = "automatic"
    config.searches = [Search(id="s", min_score=0)]
    config.profile.first_name = "A"
    config.profile.email = "a@example.com"
    store.save_config(config)
    path = tmp_path / "cv.pdf"; path.write_bytes(b"fixture")
    resume = Resume(label="Frontend", text="React frontend", path=str(path), confirmed=True, sha256=hashlib.sha256(b"fixture").hexdigest())
    store.save_resume(resume)
    j = store.add_job(Job(linkedin_id="1", title="React frontend", company="Acme", description="React frontend", status="queued", search_id="s"))
    worker = Worker(store, FixtureBrowser())
    await worker.process_one()
    assert store.job(j.id).status == "submission_uncertain"
    assert store.claim() is None


def test_saved_profile_and_pause_survive_reopening(tmp_path):
    path = tmp_path / "agent.db"
    store = Store(path)
    config = store.config()
    config.profile.first_name = "Jhon"
    store.save_config(config)
    reopened = Store(path)
    assert reopened.config().profile.first_name == "Jhon"
    assert reopened.config().settings.paused
