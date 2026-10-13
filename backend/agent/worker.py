import asyncio
import inspect
import json
import re
from datetime import datetime, timedelta, timezone

import httpx
from playwright.async_api import Error as BrowserError, TimeoutError as BrowserTimeout

from .matching import match_job, select_resume
from .models import ApplyResult, Job, now
from .store import Store, within_hours
from .forms import OperationPaused
from .operations import SleepPrevention
from .tracking import TrackingStore


def transient_failure(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, BrowserTimeout, httpx.TimeoutException, httpx.NetworkError)):
        return True
    return isinstance(exc, BrowserError) and bool(re.search(r"ERR_(?:CONNECTION|NETWORK|TIMED_OUT)|net::ERR_INTERNET_DISCONNECTED", str(exc)))


class Worker:
    def __init__(self, store: Store, browser):
        self.store, self.browser = store, browser
        self.task: asyncio.Task | None = None
        self.busy = False
        self.message = "Paused"
        self.run_lock = asyncio.Lock()
        self.sleep_prevention = SleepPrevention()
        self.tracking=TrackingStore(store)

    async def evaluate(self, job: Job, config):
        matches = []
        evaluations = []
        for search in config.searches:
            match = match_job(job, search, config.profile, aliases=config.settings.technology_aliases)
            evaluations.append({"search_id": search.id, "revision": config.revision,
                                "enabled": search.enabled, **match.model_dump()})
            if search.enabled:
                matches.append((search, match))
        self.store.save_job_evaluations(job.id, evaluations)
        job.revision = config.revision
        if job.possible_duplicate:
            job.status, job.error = "needs_input", "Possible duplicate vacancy; compare employer requisitions first"
            return None
        if not matches:
            job.status, job.error = "needs_input", "Search is missing or disabled"
            return None
        # Any eligible enabled search may qualify a canonical vacancy. Prefer
        # actionable matches, then missing-information reviews, then score.
        search, match = max(matches, key=lambda item: (item[1].eligible, item[1].needs_review, item[1].score))
        job.search_id = search.id
        job.score, job.reasons, job.revision = match.score, match.reasons, config.revision
        if not match.eligible:
            job.status = "needs_input" if match.needs_review else "rejected"
            job.error = "; ".join(match.reasons)
            return None
        verification = await self.browser.verify(job, config)
        job.evidence = verification.evidence
        if verification.state != "eligible":
            job.status = "blocked" if verification.state == "blocked" else "needs_input"
            job.error = "; ".join(verification.evidence)
            return None
        choice = select_resume(job, [r for r in self.store.resumes() if r.confirmed], config.settings.cv_min_score, aliases=config.settings.technology_aliases,profile=config.profile)
        if not choice:
            job.status, job.error = "needs_input", "No confirmed CV meets the selection threshold"
            return None
        if (job.resume_id, job.resume_hash) != (choice.resume.id, choice.resume.sha256):
            job.approved_revision = -1
        job.resume_id, job.resume_hash = choice.resume.id, choice.resume.sha256
        job.reasons.extend(choice.reasons)
        job.error = ""
        return choice.resume

    async def _reevaluate(self, config):
        resume_generation = self.store.resume_generation()
        for job in self.store.jobs(100000):
            if (job.status not in {"discovered", "rejected", "needs_input", "awaiting_review", "queued"}
                    or job.submission_started
                    or job.possible_duplicate
                    or (job.last_error_class and job.attempts >= config.settings.retry_attempts)):
                continue
            if (job.status != "discovered" and job.revision == config.revision
                    and self.store.job_resume_generation(job.id) == resume_generation):
                continue
            if self.store.manually_skipped(job.id):
                continue
            if self.store.config().revision != config.revision or self.store.resume_generation() != resume_generation:
                raise OperationPaused("Configuration or CV changed during reevaluation")
            original = job.model_copy(deep=True)
            resume = await self.evaluate(job, config)
            job.revision = config.revision
            intervention = False
            if resume:
                intervention = self.store.application_held(job.id) or original.status == 'needs_input' and (original.attempts > 0 or bool(original.questions))
                if intervention:
                    job.status, job.error = original.status, original.error
                else:
                    job.status = ("queued" if config.settings.mode == "automatic"
                                  or original.status == "queued" and job.approved_revision == config.revision
                                  else "awaiting_review")
            self.store.save_reevaluated_job(job, original, config.revision, resume_generation, hold=intervention)

    async def reevaluate(self):
        """Refresh stale unsubmitted vacancies without requiring rediscovery."""
        async with self.run_lock:
            await self._reevaluate(self.store.config())

    async def discover(self, force: bool = False):
        async with self.run_lock:
            config = self.store.config()
            if not force and (config.settings.paused or not within_hours(config.settings)):
                return
            if self.store.portal_suspended("www.linkedin.com"):
                self.message = "LinkedIn is suspended; resume it after checking the browser"
                return
            self.busy, self.message = True, "Searching LinkedIn"
            retry_pending = self.store.meta("discovery_retry_pending") == "true"
            attempt = int(self.store.meta("discovery_attempts") or "0") + 1 if retry_pending and not force else 1
            self.store.meta("discovery_attempts", str(attempt))
            self.store.meta("discovery_retry_pending", "")
            next_delay = timedelta(minutes=config.settings.interval_minutes)
            try:
                await self._reevaluate(config)
                for search in config.searches:
                    if not search.enabled or not force and self.store.config().settings.paused:
                        continue
                    kwargs = {"can_act": lambda: self.store.config().revision == config.revision and (force or not self.store.config().settings.paused)}
                    parameters = inspect.signature(self.browser.discover).parameters
                    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
                    if "max_pages" in parameters or accepts_kwargs:
                        kwargs["max_pages"] = config.settings.max_search_pages
                    if "known_ids" in parameters or accepts_kwargs:
                        kwargs["known_ids"] = self.store.linkedin_ids()
                    progress = None
                    progress_key = "discovery_progress:" + search.id
                    if "progress" in parameters or accepts_kwargs:
                        try:
                            progress = json.loads(self.store.meta(progress_key) or "{}")
                        except (ValueError, TypeError):
                            progress = {}
                        if not isinstance(progress, dict):
                            progress = {}
                        kwargs["progress"] = progress
                    try:
                        jobs = await self.browser.discover(search, config.settings.max_jobs_per_run, **kwargs)
                        for job in jobs:
                            job.search_id = search.id
                            stored = self.store.add_job(job)
                            if progress is not None:
                                from .discovery import acknowledge_candidates
                                acknowledge_candidates(progress, [job.linkedin_id])
                                self.store.meta(progress_key, json.dumps(progress))
                            if stored.status != "discovered":
                                continue
                            original = stored.model_copy(deep=True)
                            resume_generation = self.store.resume_generation()
                            resume = await self.evaluate(stored, config)
                            if resume:
                                stored.status = "queued" if config.settings.mode == "automatic" else "awaiting_review"
                            self.store.save_reevaluated_job(stored, original, config.revision, resume_generation)
                    finally:
                        if progress is not None:
                            self.store.meta(progress_key, json.dumps(progress))
                self.store.meta("last_discovery", now())
                self.store.meta("browser_issue", "")
                self.store.meta("discovery_attempts", "0")
                self.store.event("LinkedIn discovery completed")
            except OperationPaused:
                self.message = "Search paused or configuration changed"
            except Exception as exc:
                self.message = "Discovery needs attention"
                self.store.event("Discovery stopped: " + type(exc).__name__, "warning")
                self.store.meta("browser_issue", str(exc)[:300])
                if getattr(self.browser, "state", "") == "needs_login":
                    self.store.suspend_portal("www.linkedin.com", "Complete LinkedIn sign-in or verification in the browser", config.settings.portal_cooldown_minutes)
                elif transient_failure(exc) and attempt < config.settings.retry_attempts:
                    next_delay = timedelta(seconds=config.settings.retry_backoff_seconds * 2 ** (attempt - 1))
                    self.store.meta("discovery_retry_pending", "true")
            finally:
                self.store.meta("next_discovery", (datetime.now(timezone.utc) + next_delay).isoformat())
                self.busy = False

    async def process_one(self):
        async with self.run_lock:
            config = self.store.config()
            await self._reevaluate(config)
            if config.settings.paused or not within_hours(config.settings) or self.store.daily_count() >= config.settings.daily_limit:
                return
            retained = getattr(self.browser, 'pending_application', None)
            job = self.store.claim(retained.job_id if retained else None)
            if not job:
                return
            self.busy, self.message = True, "Applying to " + job.company
            attempt_id=None
            result=None
            try:
                attempt_id=self.tracking.start_attempt(job)
                resume = await self.evaluate(job, config)
                self.store.save_job(job)
                if resume is None:
                    return
                if config.settings.mode == "review" and job.approved_revision != config.revision:
                    job.status, job.error = "awaiting_review", "Review this application under your current settings before queueing"
                    self.store.save_job(job)
                    return
                if not config.profile.email or not config.profile.first_name:
                    job.status, job.error = "needs_input", "Save your name and email in Profile first"
                    self.store.save_job(job)
                    return
                self.store.save_job(job)
                result = await self.browser.apply(job, config.profile, resume, config, lambda: self.store.begin_submission(job.id, config.revision), can_act=lambda: self.store.can_act(job.id, config.revision))
                fresh = self.store.job(job.id)
                fresh.adapter_name, fresh.adapter_version = job.adapter_name, job.adapter_version
                fresh.document_ids=job.document_ids
                if fresh.submission_started and result.state != "submitted":
                    result = ApplyResult(state="submission_uncertain", reason=result.reason)
                fresh.status, fresh.error = result.state, result.reason
                fresh.retry_at = ""
                if result.portal_issue and not fresh.submission_started:
                    self.store.suspend_portal(self.store.portal_host(fresh), result.reason, config.settings.portal_cooldown_minutes)
                    fresh.last_error_class = "PortalIntervention"
                    fresh.status = "needs_input"
                elif result.retryable and result.state == "failed" and not fresh.submission_started:
                    if not self.store.schedule_retry(fresh, config.settings, "TransientBrowserFailure"):
                        fresh.status = "needs_input"
                        fresh.error += " (Attempt limit reached; inspect before queueing again.)"
                fresh.questions, fresh.answers = result.questions, result.answers
                fresh.field_details = result.field_details
                fresh.answer_sources = result.sources
                self.store.save_job(fresh)
                if fresh.status == 'needs_input':
                    self.store.hold_application(fresh.id)
                self.store.event("Application " + fresh.status, "info" if fresh.status == "submitted" else "warning", job.id)
            except Exception as exc:
                fresh = self.store.job(job.id)
                fresh.adapter_name, fresh.adapter_version = job.adapter_name, job.adapter_version
                fresh.status = "submission_uncertain" if fresh.submission_started else "needs_input"
                fresh.error = "Browser operation interrupted (" + type(exc).__name__ + "). Open the browser to inspect."
                fresh.last_error_class = type(exc).__name__
                fresh.retry_at = ""
                if not fresh.submission_started and transient_failure(exc):
                    self.store.schedule_retry(fresh, config.settings, type(exc).__name__)
                self.store.save_job(fresh)
                if fresh.status == 'needs_input':
                    self.store.hold_application(fresh.id)
                self.store.event("Application interrupted", "warning", job.id)
            finally:
                try:
                    if attempt_id:
                        outcome=self.store.job(job.id)
                        if outcome is not None:
                            if outcome.status=='applying' and result is None:
                                outcome=outcome.model_copy(deep=True)
                                outcome.status='submission_uncertain' if outcome.submission_started else 'failed'
                                outcome.error='Attempt interrupted before an outcome was recorded'
                            self.tracking.finish_attempt(attempt_id,outcome,result)
                finally:
                    self.busy = False

    async def loop(self):
        while True:
            try:
                config = self.store.config()
                await self.reevaluate()
                self.sleep_prevention.update(config.settings.prevent_sleep, not config.settings.paused and within_hours(config.settings))
                if not config.settings.paused:
                    retained = getattr(self.browser, 'pending_application', None)
                    retained_job = self.store.job(retained.job_id) if retained else None
                    if retained_job and retained_job.status == 'queued':
                        # A discovery navigation would discard the user's form.
                        await self.process_one()
                    else:
                        due = self.store.meta("next_discovery")
                        if not due or datetime.fromisoformat(due) <= datetime.now(timezone.utc):
                            await self.discover()
                        await self.process_one()
                    self.message = "Watching for matching jobs"
                else:
                    self.message = "Paused"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.message = "Worker needs attention"
                self.store.event("Worker error: " + type(exc).__name__, "error")
            await asyncio.sleep(3)

    def start(self):
        self.store.recover()
        self.task = asyncio.create_task(self.loop())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.sleep_prevention.stop()
