import importlib
import plistlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.models import ApplyResult, Job, Resume, Search
from agent.store import Store
from agent.worker import Worker


def ready(tmp_path):
    store = Store(tmp_path / 'agent.db')
    config = store.config()
    config.settings.paused = False
    config.settings.mode = 'automatic'
    config.settings.retry_backoff_seconds = 5
    config.profile.first_name, config.profile.email = 'Alex', 'alex@example.com'
    config.searches = [Search(min_score=0)]
    config = store.save_config(config)
    resume = Resume(label='CV', confirmed=True)
    store.save_resume(resume)
    return store, config, resume


def queued(store, config, number='1', host='careers.example'):
    return store.add_job(Job(linkedin_id=number, title='Engineer '+number, company='Example '+number,
                             application_url='https://'+host+'/apply/'+number, status='queued',
                             search_id=config.searches[0].id))


class Browser:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    async def apply(self, job, profile, resume, config, before_submit, can_act):
        job.adapter_name, job.adapter_version = 'greenhouse', '1'
        if self.error:
            raise self.error
        return self.result


def worker(store, resume, browser):
    instance = Worker(store, browser)
    async def evaluate(job, config):
        return resume
    instance.evaluate = evaluate
    return instance


@pytest.mark.asyncio
async def test_transient_failures_have_durable_bounded_exponential_backoff(tmp_path):
    store, config, resume = ready(tmp_path)
    job = queued(store, config)
    instance = worker(store, resume, Browser(error=TimeoutError()))
    for attempt, delay in [(1, 5), (2, 10), (3, None)]:
        before = datetime.now(timezone.utc)
        await instance.process_one()
        fresh = store.job(job.id)
        assert fresh.attempts == attempt
        assert fresh.last_error_class == 'TimeoutError'
        if delay is None:
            assert fresh.status == 'needs_input'
            assert not fresh.retry_at
        else:
            assert fresh.status == 'queued'
            assert delay-1 <= (datetime.fromisoformat(fresh.retry_at)-before).total_seconds() <= delay+1
            assert Store(store.path).claim() is None
            fresh.retry_at = (before-timedelta(seconds=1)).isoformat()
            store.save_job(fresh)


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [ValueError('invalid field'), RuntimeError('unsupported form')])
async def test_unknown_failures_are_not_automatically_retried(tmp_path, error):
    store, config, resume = ready(tmp_path)
    job = queued(store, config)
    await worker(store, resume, Browser(error=error)).process_one()
    assert store.job(job.id).status == 'needs_input'
    assert not store.job(job.id).retry_at


@pytest.mark.asyncio
async def test_submission_start_prevents_retries_even_when_browser_requests_retry(tmp_path):
    store, config, resume = ready(tmp_path)
    job = queued(store, config)
    class SubmittingBrowser(Browser):
        async def apply(self, job, *args, **kwargs):
            fresh = store.job(job.id)
            fresh.submission_started = True
            store.save_job(fresh)
            return ApplyResult(state='failed', retryable=True, reason='timeout')
    await worker(store, resume, SubmittingBrowser()).process_one()
    fresh = store.job(job.id)
    assert fresh.status == 'submission_uncertain'
    assert not fresh.retry_at
    assert store.claim() is None


@pytest.mark.asyncio
async def test_portal_cooldown_survives_restart_and_other_portals_continue(tmp_path):
    store, config, resume = ready(tmp_path)
    first = queued(store, config)
    blocked = queued(store, config, '2')
    other = queued(store, config, '3', 'jobs.example')
    await worker(store, resume, Browser(ApplyResult(state='needs_input', portal_issue=True, reason='Sign in'))).process_one()
    restarted = Store(store.path)
    assert restarted.portals()[0]['host'] == 'careers.example'
    assert restarted.claim().id == other.id
    restarted.resume_portal('careers.example')
    assert restarted.claim().id == first.id
    assert restarted.job(blocked.id).status == 'queued'


@pytest.mark.asyncio
async def test_result_retry_and_adapter_stamp_are_persisted(tmp_path):
    store, config, resume = ready(tmp_path)
    job = queued(store, config)
    await worker(store, resume, Browser(ApplyResult(state='failed', retryable=True, reason='Transient timeout'))).process_one()
    fresh = store.job(job.id)
    assert fresh.status == 'queued'
    assert fresh.adapter_name == 'greenhouse'
    assert fresh.adapter_version == '1'


def test_recovery_does_not_exceed_attempt_limit(tmp_path):
    store, config, _ = ready(tmp_path)
    job = queued(store, config)
    job.status, job.attempts = 'applying', 3
    store.save_job(job)
    Store(store.path).recover()
    assert store.job(job.id).status == 'needs_input'


@pytest.mark.asyncio
async def test_discovery_receives_page_limit_and_old_stub_remains_usable(tmp_path):
    store, config, _ = ready(tmp_path)
    seen = []
    class PagedBrowser:
        async def discover(self, search, limit, can_act, *, max_pages):
            seen.append(max_pages)
            return []
    await Worker(store, PagedBrowser()).discover()
    assert seen == [20]
    class LegacyBrowser:
        async def discover(self, search, limit, can_act):
            seen.append('legacy')
            return []
    await Worker(store, LegacyBrowser()).discover()
    assert seen == [20, 'legacy']


def operations():
    return importlib.import_module('agent.operations')


def test_startup_is_inspectable_loopback_bound_and_only_activated_by_explicit_control(tmp_path, monkeypatch):
    ops = operations()
    monkeypatch.setattr(ops.sys, 'platform', 'darwin')
    home = tmp_path / 'home'
    monkeypatch.setattr(ops.Path, 'home', lambda: home)
    calls = []
    monkeypatch.setattr(ops.subprocess, 'run', lambda args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0))
    root = tmp_path / 'project with spaces'
    root.mkdir()
    status = ops.startup_status(root)
    assert not status['enabled']
    assert calls == []
    status = ops.configure_startup(root, True)
    assert status['enabled']
    data = plistlib.loads(Path(status['config_path']).read_bytes())
    assert data['WorkingDirectory'] == str(root)
    assert data['ProgramArguments'] == ['/bin/bash', str(root / 'scripts/start.sh')]
    assert data['RunAtLoad'] is True
    assert data.get('KeepAlive', False) is False
    assert Path(status['launch_agent_path']).is_file()
    assert calls[0][0:2] == ['launchctl', 'bootstrap']
    status = ops.configure_startup(root, False)
    assert not status['enabled']
    assert not Path(status['launch_agent_path']).exists()


def test_sleep_prevention_lifecycle_and_unsupported_status(monkeypatch):
    ops = operations()
    monkeypatch.setattr(ops.sys, 'platform', 'darwin')
    class Process:
        pid = 123
        def __init__(self): self.ended = False
        def poll(self): return 0 if self.ended else None
        def terminate(self): self.ended = True
        def wait(self, timeout): return 0
    processes = []
    def popen(args, **kwargs):
        assert args[0] == '/usr/bin/caffeinate'
        assert kwargs.get('shell', False) is False
        process = Process()
        processes.append(process)
        return process
    monkeypatch.setattr(ops.subprocess, 'Popen', popen)
    prevention = ops.SleepPrevention()
    assert not prevention.update(True, False)['active']
    assert prevention.update(True, True)['active']
    prevention.update(True, True)
    assert len(processes) == 1
    assert not prevention.update(False, True)['active']
    assert processes[0].ended
    monkeypatch.setattr(ops.sys, 'platform', 'linux')
    status = prevention.update(True, True)
    assert not status['supported']
    assert not status['active']


def test_starlette_client_import_has_no_dependency_deprecation():
    result = subprocess.run([sys.executable, '-W', 'error', '-c', 'from starlette.testclient import TestClient'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_discovery_transient_retries_are_durable_bounded_and_exponential(tmp_path):
    store, config, _ = ready(tmp_path)
    class InterruptedBrowser:
        async def discover(self, search, limit, can_act, *, max_pages):
            raise TimeoutError('connection')
    for attempt, delay in [(1, 5), (2, 10), (3, 1800)]:
        before = datetime.now(timezone.utc)
        await Worker(Store(store.path), InterruptedBrowser()).discover()
        assert int(store.meta('discovery_attempts')) == attempt
        assert delay-1 <= (datetime.fromisoformat(store.meta('next_discovery'))-before).total_seconds() <= delay+1
    assert store.meta('discovery_retry_pending') == ''


@pytest.mark.asyncio
async def test_discovery_login_challenge_suspends_only_linkedin_without_retry(tmp_path):
    store, config, _ = ready(tmp_path)
    class LoginBrowser:
        state = 'needs_login'
        async def discover(self, search, limit, can_act, *, max_pages):
            raise RuntimeError('Sign in first')
    await Worker(store, LoginBrowser()).discover()
    assert store.portal_suspended('www.linkedin.com')
    assert store.meta('discovery_retry_pending') == ''
    other = queued(store, config, 'other', 'jobs.example')
    assert store.claim().id == other.id


def test_resume_portal_requeues_only_intervention_jobs_without_submission(tmp_path):
    store, config, _ = ready(tmp_path)
    jobs = []
    for number, error_class, uncertain in [('1', 'PortalIntervention', False), ('2', 'ValueError', False), ('3', 'PortalIntervention', True)]:
        job = queued(store, config, number)
        job.status, job.last_error_class, job.submission_started = 'needs_input', error_class, uncertain
        store.save_job(job)
        jobs.append(job)
    store.suspend_portal('careers.example', 'Login challenge', 30)
    store.resume_portal('careers.example')
    assert [store.job(job.id).status for job in jobs] == ['queued', 'needs_input', 'needs_input']
    assert store.portals() == []


@pytest.mark.asyncio
async def test_discovery_classifies_playwright_connection_errors_but_not_selector_errors(tmp_path):
    from playwright.async_api import Error
    store, _, _ = ready(tmp_path)
    class InterruptedBrowser:
        error = Error('Page.goto: net::ERR_CONNECTION_RESET at https://www.linkedin.com')
        async def discover(self, search, limit, can_act, *, max_pages):
            raise self.error
    browser = InterruptedBrowser()
    await Worker(store, browser).discover()
    assert store.meta('discovery_retry_pending') == 'true'
    browser.error = Error('Locator.click: strict mode violation')
    await Worker(store, browser).discover()
    assert store.meta('discovery_retry_pending') == ''


def test_resolved_portal_failure_cannot_requeue_a_later_unknown_answer(tmp_path):
    store, config, _ = ready(tmp_path)
    job = queued(store, config)
    job.status, job.last_error_class = 'needs_input', 'PortalIntervention'
    store.save_job(job)
    store.resume_portal('careers.example')
    job = store.claim()
    job.status, job.error = 'needs_input', 'Required unknown qualification'
    store.save_job(job)
    store.resume_portal('careers.example')
    assert store.job(job.id).status == 'needs_input'
