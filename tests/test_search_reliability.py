"""Canonical vacancies retain independent search decisions and safe reevaluation."""
import pytest

from agent.models import Job, Resume, Search, Verification
from agent.store import Store
from agent.worker import Worker


class SearchBrowser:
    async def verify(self, job, config):
        return Verification(state="eligible", evidence=["Fixture employer"])

    async def discover(self, search, limit, can_act):
        return [Job(linkedin_id="shared", title="Python engineer", company="Acme", description="Python", country="Canada")]


def ready(tmp_path, *, mode="automatic", searches=None):
    store = Store(tmp_path / "agent.db")
    config = store.config()
    config.settings.mode = mode
    config.searches = searches or [Search(id="first", required_skills=["Rust"], min_score=0)]
    config = store.save_config(config)
    store.save_resume(Resume(label="Python CV", confirmed=True, text="Python engineer", sha256="fixture"))
    return store, config, Worker(store, SearchBrowser())


@pytest.mark.asyncio
async def test_overlapping_searches_keep_independent_decisions_on_one_canonical_job(tmp_path):
    store, config, worker = ready(tmp_path, searches=[
        Search(id="first", required_skills=["Rust"], min_score=0),
        Search(id="second", required_skills=["Python"], min_score=0),
    ])
    await worker.discover(force=True)
    assert len(store.jobs()) == 1
    job = store.jobs()[0]
    assert job.status == "queued"
    decisions = {item["search_id"]: item for item in Store(store.path).job_evaluations(job.id)}
    assert decisions["first"]["eligible"] is False
    assert decisions["second"]["eligible"] is True
    assert decisions["first"]["revision"] == config.revision
    assert job.search_id == "second"


@pytest.mark.asyncio
async def test_disabling_origin_search_keeps_other_enabled_search_eligible(tmp_path):
    store, config, worker = ready(tmp_path, searches=[Search(id="first", min_score=0), Search(id="second", min_score=0)])
    await worker.discover(force=True)
    config.searches[0].enabled = False
    config = store.save_config(config)
    await worker.reevaluate()
    job = store.jobs()[0]
    assert job.status == "queued"
    assert job.search_id == "second"
    decisions = {item["search_id"]: item for item in store.job_evaluations(job.id)}
    assert decisions["first"]["enabled"] is False
    assert decisions["second"]["revision"] == config.revision


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["rejected", "needs_input"])
async def test_changed_search_reconsiders_unsubmitted_jobs_without_rediscovery(tmp_path, initial):
    store, config, worker = ready(tmp_path)
    job = store.add_job(Job(linkedin_id="1", title="Python engineer", company="Acme", description="Python", status=initial, search_id="first", revision=config.revision))
    config.searches[0].required_skills = ["Python"]
    config = store.save_config(config)
    await worker.reevaluate()
    fresh = store.job(job.id)
    assert fresh.status == "queued"
    assert fresh.revision == config.revision
    assert len(store.jobs()) == 1


@pytest.mark.asyncio
async def test_automatic_processing_reevaluates_rejected_jobs_even_while_paused(tmp_path):
    store, config, worker = ready(tmp_path)
    await worker.discover(force=True)
    job = store.jobs()[0]
    assert job.status == "rejected"
    config.searches[0].required_skills = ["Python"]
    store.save_config(config)
    await worker.process_one()
    assert store.job(job.id).status == "queued"


@pytest.mark.asyncio
async def test_manual_skip_is_durable_across_settings_changes_and_explicit_queue_clears_it(tmp_path):
    store, config, worker = ready(tmp_path)
    job = store.add_job(Job(linkedin_id="1", title="Python engineer", company="Acme", search_id="first"))
    store.job_action(job.id, "skip")
    # Error text is presentation, not the durable indication of a manual skip.
    job = store.job(job.id)
    job.error = "Dismissed"
    store.save_job(job)
    config.searches[0].required_skills = []
    store.save_config(config)
    restarted = Store(store.path)
    await Worker(restarted, SearchBrowser()).reevaluate()
    assert restarted.job(job.id).status == "rejected"
    restarted.job_action(job.id, "queue")
    config = restarted.config()
    config.profile.skills = ["Python"]
    restarted.save_config(config)
    await Worker(restarted, SearchBrowser()).reevaluate()
    assert restarted.job(job.id).status == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,started", [("applying", False), ("submitted", True), ("submission_uncertain", True), ("blocked", False), ("queued", True), ("failed", False)])
async def test_reevaluation_preserves_active_confirmed_uncertain_and_terminal_states(tmp_path, status, started):
    store, config, worker = ready(tmp_path)
    job = store.add_job(Job(linkedin_id="1", title="Python engineer", company="Acme", status=status, submission_started=started, revision=config.revision, search_id="first"))
    config.searches[0].required_skills = []
    store.save_config(config)
    await worker.reevaluate()
    assert store.job(job.id).model_dump() == job.model_dump()


@pytest.mark.asyncio
async def test_changed_settings_in_review_mode_require_new_approval(tmp_path):
    store, config, worker = ready(tmp_path, mode="review", searches=[Search(id="first", min_score=0)])
    await worker.discover(force=True)
    job = store.jobs()[0]
    store.job_action(job.id, "queue")
    config.profile.skills = ["Python"]
    config = store.save_config(config)
    await worker.reevaluate()
    fresh = store.job(job.id)
    assert fresh.status == "awaiting_review"
    assert fresh.approved_revision != config.revision


@pytest.mark.asyncio
async def test_reevaluation_preserves_retry_backoff_and_exhausted_attempts(tmp_path):
    store, config, worker = ready(tmp_path, searches=[Search(id="first", min_score=0)])
    job = store.add_job(Job(linkedin_id="1", title="Python engineer", company="Acme", status="queued", attempts=2, retry_at="2999-01-01T00:00:00+00:00", last_error_class="TimeoutError", revision=config.revision, search_id="first"))
    exhausted = store.add_job(Job(linkedin_id="2", title="Python engineer 2", company="Other", status="needs_input", attempts=3, last_error_class="TimeoutError", revision=config.revision, search_id="first"))
    config.profile.skills = ["Python"]
    store.save_config(config)
    await worker.reevaluate()
    fresh = store.job(job.id)
    assert (fresh.attempts, fresh.retry_at, fresh.last_error_class) == (2, "2999-01-01T00:00:00+00:00", "TimeoutError")
    assert store.job(exhausted.id).status == "needs_input"
    assert store.claim() is None


@pytest.mark.asyncio
async def test_skip_during_verification_cannot_be_overwritten_by_reevaluation(tmp_path):
    store, config, worker = ready(tmp_path, searches=[Search(id="first", min_score=0)])
    job = store.add_job(Job(linkedin_id="1", title="Python engineer", company="Acme", status="rejected", revision=config.revision, search_id="first"))
    class SkipBrowser(SearchBrowser):
        async def verify(self, candidate, config):
            store.job_action(candidate.id, "skip")
            return await super().verify(candidate, config)
    config.profile.skills = ["Python"]
    store.save_config(config)
    await Worker(store, SkipBrowser()).reevaluate()
    assert store.job(job.id).status == "rejected"
    assert store.job(job.id).error == "Skipped by user"


def test_known_listing_ids_include_aliases_of_one_canonical_vacancy(tmp_path):
    store = Store(tmp_path / "agent.db")
    first = store.add_job(Job(linkedin_id="first", title="Python engineer", company="Acme", application_url="https://jobs.example/acme/1"))
    second = store.add_job(Job(linkedin_id="second", title="Python engineer", company="Acme", application_url="https://jobs.example/acme/1"))
    assert second.id == first.id
    assert Store(store.path).linkedin_ids() == {"first", "second"}


@pytest.mark.asyncio
async def test_discovery_passes_and_persists_optional_progress_even_on_failure(tmp_path):
    import json
    store, config, _ = ready(tmp_path)
    store.add_job(Job(linkedin_id="known", title="Prior", company="Other"))
    class ProgressBrowser(SearchBrowser):
        async def discover(self, search, limit, can_act, *, known_ids, progress):
            assert "known" in known_ids
            progress["page"] = progress.get("page", 0) + 1
            raise TimeoutError("Fixture interruption")
    worker = Worker(store, ProgressBrowser())
    await worker.discover(force=True)
    assert json.loads(store.meta("discovery_progress:first"))["page"] == 1
    await Worker(Store(store.path), ProgressBrowser()).discover(force=True)
    assert json.loads(store.meta("discovery_progress:first"))["page"] == 2


@pytest.mark.asyncio
async def test_confirming_cv_automatically_recovers_no_confirmed_cv_job(tmp_path):
    store, config, worker = ready(tmp_path, searches=[Search(id="first", min_score=0)])
    resume = store.resumes()[0]
    resume.confirmed = False
    store.save_resume(resume)
    await worker.discover(force=True)
    job = store.jobs()[0]
    assert job.status == "needs_input"
    assert "No confirmed CV" in job.error
    resume.confirmed = True
    store.save_resume(resume)
    await worker.reevaluate()
    assert store.job(job.id).status == "queued"
    assert store.config().revision == config.revision


@pytest.mark.asyncio
async def test_selected_cv_change_requires_new_review_approval(tmp_path):
    store, config, worker = ready(tmp_path, mode="review", searches=[Search(id="first", min_score=0)])
    await worker.discover(force=True)
    job = store.jobs()[0]
    store.job_action(job.id, "queue")
    resume = store.resumes()[0]
    resume.sha256 = "new-content"
    store.save_resume(resume)
    await worker.reevaluate()
    fresh = store.job(job.id)
    assert fresh.resume_hash == "new-content"
    assert fresh.status == "awaiting_review"
    assert fresh.approved_revision != config.revision


@pytest.mark.asyncio
async def test_unchanged_cv_write_does_not_dirty_jobs_again(tmp_path):
    store, config, worker = ready(tmp_path, searches=[Search(id="first", min_score=0)])
    await worker.discover(force=True)
    original = store.jobs()[0]
    store.save_resume(store.resumes()[0])
    await worker.reevaluate()
    assert store.job(original.id).model_dump() == original.model_dump()
