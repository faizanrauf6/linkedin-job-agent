from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import keyring
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock, Timeout as LockTimeout
from pydantic import BaseModel, Field

from .browser import BrowserAgent
from .models import Config, Model, now
from .resumes import MAX_UPLOAD, import_resume, public_resume
from .security import hostname, public_url
from .store import Store
from .worker import Worker

ROOT = Path(__file__).resolve().parents[2]
ORIGINS = {"http://127.0.0.1:8765", "http://localhost:8765", "http://127.0.0.1:5173", "http://localhost:5173", "http://testserver"}


class Control(Model):
    action: Literal["pause", "resume", "search"]


class ResumeUpdate(Model):
    label: str | None = None
    skills: list[str] | None = None
    roles: list[str] | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    confirmed: bool | None = None
    conflict_acknowledged: bool | None = None
    conflict_fingerprint: str | None = None


class JobAction(Model):
    action: Literal["queue", "skip", "mark_submitted", "retry_unsubmitted", "distinct_vacancy"]
    confirmation: bool = False


class Credential(Model):
    kind: Literal["portal", "model"] = "portal"
    target: str
    username: str = ""
    secret: str = Field(min_length=1, max_length=10000)


class DataDelete(Model):
    confirmation: str


class StartupUpdate(Model):
    enabled: bool


class ContinuationApproval(Model):
    token: str = Field(min_length=1, max_length=128)
    confirmation: bool = False


class CheckpointApproval(Model):
    confirmation: bool = False
    answers: dict[str,str] = Field(default_factory=dict)


def create_app(runtime: Path | None = None, run_worker: bool = True) -> FastAPI:
    runtime = (runtime or ROOT / ".runtime").resolve()
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime.chmod(0o700)
    store = Store(runtime / "agent.db")
    browser = BrowserAgent(runtime, store=store)
    worker = Worker(store, browser)
    pending_tasks = set()
    process_lock = FileLock(runtime / "agent.lock")

    @asynccontextmanager
    async def lifespan(app):
        if run_worker:
            try:
                process_lock.acquire(timeout=0)
            except LockTimeout as exc:
                raise RuntimeError("Another agent is using this runtime directory") from exc
            worker.start()
        try:
            yield
        finally:
            for task in list(pending_tasks):
                task.cancel()
            await asyncio.gather(*pending_tasks, return_exceptions=True)
            await worker.stop()
            await browser.close()
            if run_worker:
                process_lock.release()

    app = FastAPI(title="Orbit Local Job Agent", lifespan=lifespan)
    app.state.store, app.state.worker, app.state.browser = store, worker, browser

    def refresh_conflicts():
        from .resumes import assess_resume
        profile, resumes = store.config().profile, store.resumes()
        for resume in resumes:
            assess_resume(resume, profile, [r for r in resumes if r.id != resume.id])
        for resume in resumes:
            facts = {"profile": {k: getattr(profile, k) for k in ("first_name", "last_name", "email", "phone", "linkedin_url")},
                     "cv": resume.identity_facts,
                     "peers": sorted([json.dumps(r.identity_facts, sort_keys=True) for r in resumes if r.id != resume.id and r.identity_facts])}
            fingerprint = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
            if resume.conflicts and resume.conflict_fingerprint != fingerprint:
                resume.conflict_acknowledged = False
            if resume.conflicts and not resume.conflict_acknowledged:
                resume.confirmed = False
            resume.conflict_fingerprint = fingerprint
            store.save_resume(resume)

    # Reassess legacy confirmed CVs before the worker can start using them.
    refresh_conflicts()

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in {"127.0.0.1:8765", "localhost:8765", "127.0.0.1:5173", "localhost:5173", "testserver"}:
            return JSONResponse({"detail": "Local access only"}, status_code=403)
        if request.url.path.startswith("/api"):
            origin = request.headers.get("origin")
            if origin is not None and origin not in ORIGINS or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-origin access denied"}, status_code=403)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                size = request.headers.get("content-length")
                if request.headers.get("content-type", "").startswith("multipart/") and size is None:
                    return JSONResponse({"detail": "Content-Length is required for uploads"}, status_code=411)
                from .maintenance import MAX_ARCHIVE_BYTES
                limit = MAX_ARCHIVE_BYTES if request.url.path == "/api/maintenance/restore" else MAX_UPLOAD
                if size and (not size.isdigit() or int(size) > limit + 1024 * 1024):
                    return JSONResponse({"detail": "Request exceeds upload limit"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/config")
    def get_config():
        return store.config()

    @app.put("/api/config")
    def put_config(config: Config):
        try:
            result = store.save_config(config)
            refresh_conflicts()
            store.meta("live_preparation_check", "")
            store.event("Profile and configuration saved")
            return result
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/status")
    def status():
        config = store.config()
        counts = Counter(j.status for j in store.jobs(100000))
        resumes = store.resumes()
        return {
            "paused": config.settings.paused, "mode": config.settings.mode,
            "busy": worker.busy, "message": worker.message, "browser": browser.state,
            "counts": dict(counts), "daily_attempts": store.daily_count(), "daily_limit": config.settings.daily_limit,
            "last_discovery": store.meta("last_discovery"), "next_discovery": store.meta("next_discovery"),
            "browser_issue": store.meta("browser_issue"),
            "setup": {"profile": bool(config.profile.first_name and config.profile.email), "searches": any(s.enabled for s in config.searches), "resumes": any(r.confirmed for r in resumes)},
        }

    def spawn(coro):
        task = asyncio.create_task(coro)
        pending_tasks.add(task)
        task.add_done_callback(pending_tasks.discard)

    @app.post("/api/control")
    async def control(body: Control):
        config = store.config()
        if body.action == "search":
            if worker.busy or worker.run_lock.locked():
                raise HTTPException(409, "The agent is already working")
            if not any(s.enabled for s in config.searches):
                raise HTTPException(409, "Add and enable a search first")
            spawn(worker.discover(force=True))
            return {"ok": True}
        if body.action == "resume" and (not config.profile.first_name or not config.profile.email or not any(s.enabled for s in config.searches) or not any(r.confirmed for r in store.resumes())):
            raise HTTPException(409, "Complete your profile, enable a search, and confirm at least one CV first")
        config.settings.paused = body.action == "pause"
        try:
            store.save_config(config)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        store.event("Agent " + ("paused" if config.settings.paused else "resumed"))
        return {"ok": True}

    @app.get("/api/jobs")
    def jobs():
        return store.jobs()

    @app.post("/api/jobs/{id}/action")
    def job_action(id: str, body: JobAction):
        try:
            return store.job_action(id, body.action, body.confirmation)
        except KeyError as exc:
            raise HTTPException(404, "Job not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/events")
    def events():
        return store.events()

    @app.get("/api/portals")
    def portals():
        return store.portals()

    @app.get('/api/portals/capabilities')
    def portal_capabilities():
        from .portal_capabilities import capability_registry
        return capability_registry()

    @app.post("/api/portals/{host}/resume")
    def resume_portal(host: str):
        from .models import Settings
        try:
            host = Settings.validate_host(host)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        store.resume_portal(host)
        return {"ok": True}

    @app.get("/api/startup")
    def startup():
        from .operations import startup_status
        return startup_status(ROOT)

    @app.post("/api/startup")
    def set_startup(body: StartupUpdate):
        from .operations import configure_startup
        try:
            return configure_startup(ROOT, body.enabled)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise HTTPException(503, "Startup configuration could not be applied. Check OS permissions.") from exc

    @app.get("/api/validation")
    def validation():
        config = store.config()
        def recorded(key):
            value = store.meta(key)
            return json.loads(value) if value else None
        return {"setup": {"profile": bool(config.profile.first_name and config.profile.email),
                          "resumes": any(r.confirmed for r in store.resumes()),
                          "searches": any(s.enabled for s in config.searches)},
                "session": recorded("live_session_check"), "preparation": recorded("live_preparation_check"),
                "confirmed_submissions": sum(j.status == "submitted" for j in store.jobs(100000)),
                "note": "Session and preparation checks do not submit applications or create accounts."}

    @app.post("/api/validation/session")
    async def check_session():
        if worker.busy or worker.run_lock.locked() or not store.config().settings.paused:
            raise HTTPException(409, "Pause the agent and wait for current work before testing the session")
        async with worker.run_lock:
            try:
                result = await browser.check_session()
            except Exception as exc:
                raise HTTPException(503, "Session check could not finish. Open LinkedIn and inspect the browser.") from exc
            result["checked_at"] = now()
            store.meta("live_session_check", json.dumps(result))
            return result

    @app.post("/api/jobs/{id}/prepare")
    async def prepare_application(id: str):
        if worker.busy or worker.run_lock.locked() or not store.config().settings.paused:
            raise HTTPException(409, "Pause the agent and wait for current work before preparing an application")
        job = store.job(id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job.submission_started or job.status in {"submitted", "submission_uncertain", "applying", "blocked"}:
            raise HTTPException(409, "This application cannot be prepared in its current state")
        config = store.config()
        if not config.profile.first_name or not config.profile.email:
            raise HTTPException(409, "Save your name and email first")
        original = job.model_copy(deep=True)
        resume_generation = store.resume_generation()
        async with worker.run_lock:
            worker.busy = True
            try:
                resume = await worker.evaluate(job, config)
                if resume is None:
                    if not store.save_reevaluated_job(job, original, config.revision, resume_generation):
                        raise HTTPException(409, 'The application, settings or CV changed while preparing. Review them again.')
                    raise HTTPException(409, job.error or "Application needs attention")
                result = await browser.apply(job, config.profile, resume, config, lambda: False,
                    can_act=lambda: store.config().revision == config.revision and store.config().settings.paused,
                    dry_run=True)
                job.answers, job.answer_sources, job.questions = result.answers, result.sources, result.questions
                job.field_details = result.field_details
                job.error = result.reason
                if result.state == 'blocked':
                    job.status = 'blocked'
                elif result.questions:
                    job.status = 'needs_input'
                elif job.status == 'queued':
                    job.status = 'awaiting_review'
                if not store.save_reevaluated_job(job, original, config.revision, resume_generation, hold=True):
                    raise HTTPException(409, 'The application, settings or CV changed while preparing. Review them again.')
                report = {"job_id": job.id, "state": result.state, "details": result.reason,
                          "checked_at": now(), "submitted": False, "revision": config.revision}
                store.meta("live_preparation_check", json.dumps(report))
                return report
            finally:
                worker.busy = False

    @app.get("/api/resumes")
    def resumes():
        return [public_resume(r) for r in store.resumes()]

    def continuation_context(id):
        config = store.config()
        if worker.busy or worker.run_lock.locked() or not config.settings.paused:
            raise HTTPException(409, 'Pause the agent and wait for current work before reviewing browser answers')
        job = store.job(id)
        if not job:
            raise HTTPException(404, 'Job not found')
        if job.submission_started or job.possible_duplicate or job.status not in {'needs_input', 'awaiting_review', 'failed', 'discovered'}:
            raise HTTPException(409, 'This application cannot be continued in its current state')
        resume = next((r for r in store.resumes() if r.id == job.resume_id and r.confirmed and r.sha256 == job.resume_hash), None)
        if resume is None:
            raise HTTPException(409, 'The selected confirmed CV changed. Prepare this application again.')
        return job, config, resume

    @app.get('/api/jobs/{id}/continuation')
    async def get_continuation(id: str):
        job, config, resume = continuation_context(id)
        async with worker.run_lock:
            worker.busy = True
            try:
                async with browser.lock:
                    return await browser.get_continuation(job, config, resume)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            finally:
                worker.busy = False

    @app.get('/api/jobs/{id}/checkpoint')
    def get_checkpoint(id: str):
        if not store.job(id):
            raise HTTPException(404, 'Job not found')
        return store.checkpoint(id)

    @app.post('/api/jobs/{id}/continue')
    async def continue_checkpoint(id: str, body: CheckpointApproval):
        from .continuation import checkpoint_binding
        job, config, resume = continuation_context(id)
        generation = store.resume_generation()
        saved = store.checkpoint(id)
        if not body.confirmation:
            raise HTTPException(409, 'Explicitly confirm your answers before continuing')
        if not saved or saved.get('binding') != checkpoint_binding(job, config, resume):
            raise HTTPException(409, 'The checkpoint is missing or stale. Prepare this application again.')
        updated = json.loads(json.dumps(saved))
        fields = {f['key']:f for f in saved.get('field_details',[])}
        for key,value in body.answers.items():
            detail = fields.get(key)
            choices_valid=True
            if detail and detail.get('options'):
                if detail['type'] in {'select-multiple','listbox'}:
                    try:selected=json.loads(value)
                    except ValueError:selected=[value]
                    choices_valid=isinstance(selected,list) and bool(selected) and all(isinstance(v,str) and v in detail['options'] for v in selected)
                else:choices_valid=value in detail['options']
            if (not detail or detail.get('personal_action') or detail.get('ambiguous') or detail['type'] in {'file','password'}
                    or not value.strip() or len(value) > min(detail.get('max_length') or 10000,10000)
                    or not choices_valid):
                raise HTTPException(409, 'An answer does not match the recorded question or available options')
        updated.setdefault('confirmed',{}).setdefault(saved['step_signature'],{}).update(body.answers)
        async with worker.run_lock:
            try:
                result = store.job_action(id,'queue',expected=job,revision=config.revision,
                    resume_generation=generation,checkpoint_update=(saved,updated))
                retained=browser.pending_application
                if retained is not None and retained.job_id==id:
                    retained.confirmed=updated['confirmed']
                    retained.approved={}
                store.event('Checkpoint answers confirmed; application queued for continuation',job_id=id)
                return result
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from exc

    @app.post('/api/jobs/{id}/continuation')
    async def confirm_continuation(id: str, body: ContinuationApproval):
        job, config, resume = continuation_context(id)
        resume_generation = store.resume_generation()
        if not body.confirmation:
            raise HTTPException(409, 'Explicitly confirm the displayed browser answers before continuing')
        async with worker.run_lock:
            worker.busy = True
            try:
                async with browser.lock:
                    await browser.confirm_continuation(job, config, resume, body.token)
                result = store.job_action(id, 'queue', expected=job, revision=config.revision,
                    resume_generation=resume_generation)
                store.event('Current browser answers confirmed; application queued', job_id=id)
                return result
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            finally:
                worker.busy = False

    @app.post("/api/resumes")
    async def upload(file: UploadFile = File(...), label: str = Form("")):
        data = await file.read(MAX_UPLOAD + 1)
        await file.close()
        try:
            resume = await asyncio.to_thread(import_resume, data, file.filename or "", label, runtime)
            store.save_resume(resume)
            refresh_conflicts()
            store.event("CV uploaded; text confirmation required")
            return public_resume(next(r for r in store.resumes() if r.id == resume.id))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.patch("/api/resumes/{id}")
    def update_resume(id: str, body: ResumeUpdate):
        refresh_conflicts()
        resume = next((r for r in store.resumes() if r.id == id), None)
        if not resume:
            raise HTTPException(404, "CV not found")
        if resume.conflicts and body.conflict_acknowledged and body.conflict_fingerprint != resume.conflict_fingerprint:
            raise HTTPException(409, "CV identity evidence changed. Reload and review the current conflicts before confirming")
        updated = resume.model_copy(update=body.model_dump(exclude_none=True))
        if updated.confirmed and updated.conflicts and not updated.conflict_acknowledged:
            raise HTTPException(409, "Review and acknowledge the CV identity conflicts before confirming")
        store.save_resume(updated)
        return public_resume(updated)

    @app.delete("/api/resumes/{id}")
    def delete_resume(id: str):
        resume = next((r for r in store.resumes() if r.id == id), None)
        if not resume:
            raise HTTPException(404, "CV not found")
        try:
            store.delete_resume(id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        Path(resume.path).unlink(missing_ok=True)
        refresh_conflicts()
        return {"ok": True}

    @app.post("/api/browser/{action}")
    async def browser_action(action: Literal["login", "inspect", "disconnect"]):
        if worker.busy or worker.run_lock.locked():
            raise HTTPException(409, "Pause and wait for the current operation before controlling the browser")
        try:
            if action == "login":
                await browser.open_login()
            elif action == "inspect":
                await browser.inspect()
            else:
                config = store.config()
                config.settings.paused = True
                store.save_config(config)
                await browser.close()
                shutil.rmtree(runtime / "browser", ignore_errors=True)
                store.meta("live_session_check", "")
                store.meta("live_preparation_check", "")
            store.meta("browser_issue", "")
            return {"ok": True, "state": browser.state}
        except Exception as exc:
            raise HTTPException(503, "Browser could not open. Run setup to install Chromium and inspect the server terminal.") from exc

    @app.post("/api/credentials")
    async def credentials(body: Credential):
        if body.kind == "portal":
            target = body.target if "://" in body.target else "https://" + body.target
            if not public_url(target, resolve=False) or not body.username:
                raise HTTPException(400, "Enter an exact public portal hostname and username")
            service, account = "linkedin-local-agent:portal", hostname(target)
            value = json.dumps({"username": body.username, "password": body.secret})
        else:
            target = body.target.rstrip("/")
            if target != store.config().settings.model_base_url.rstrip("/"):
                raise HTTPException(400, "Save the model endpoint in settings first")
            service, account, value = "linkedin-local-agent:model", target, body.secret
        try:
            await asyncio.to_thread(keyring.set_password, service, account, value)
        except keyring.errors.KeyringError as exc:
            raise HTTPException(503, "The OS credential store is unavailable") from exc
        return {"ok": True}

    @app.delete("/api/data")
    async def delete_data(body: DataDelete):
        if body.confirmation != "DELETE LOCAL DATA":
            raise HTTPException(400, "Type DELETE LOCAL DATA to confirm")
        if worker.busy or worker.run_lock.locked() or not store.config().settings.paused:
            raise HTTPException(409, "Pause the agent and wait for current work to finish first")
        async with worker.run_lock:
            if worker.busy or not store.config().settings.paused:
                raise HTTPException(409, "Pause the agent and wait for current work to finish first")
            await browser.close()
            for directory in ("resumes", "documents", "browser"):
                shutil.rmtree(runtime / directory, ignore_errors=True)
            store.clear()
        return {"ok": True, "note": "OS keychain credentials are separate; remove them in the operating system credential manager."}

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": "0.1.0"}

    frontend = ROOT / "frontend" / "dist"
    from .document_api import register_document_routes
    register_document_routes(app,store,worker,runtime)
    from .approval_api import register_approval_api
    browser.review_payload=register_approval_api(app,store,worker,runtime).payload_matches
    from .written_document_api import register_written_document_routes
    register_written_document_routes(app,store,worker,runtime)
    from .tracking_api import register_tracking_routes
    register_tracking_routes(app,store,worker,runtime)
    from .email_api import register_email_routes
    register_email_routes(app,store,worker,runtime)
    from .maintenance_api import register_maintenance_routes
    register_maintenance_routes(app,store,worker,browser,runtime)
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="dashboard")
    return app
