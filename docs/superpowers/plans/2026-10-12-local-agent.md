# Local LinkedIn Agent Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. The user has explicitly requested implementation in this session.

**Goal:** Deliver a runnable local dashboard and persistent automation engine for LinkedIn-discovered applications.
**Architecture:** FastAPI and SQLite own configuration, application state, and a sequential worker. React provides setup and monitoring. Playwright adapters discover LinkedIn jobs and operate supported forms; deterministic policies and grounded model answers gate submission.
**Tech Stack:** Python, FastAPI, SQLite, Playwright, React, TypeScript, Vite.
**Spec:** `docs/superpowers/specs/2026-10-12-linkedin-local-agent-design.md`

## Global Constraints

- LinkedIn is the only discovery source; supported external application destinations are allowed.
- Run locally and preserve state across restarts; no real applications during automated tests.
- Never fabricate applicant facts, bypass authentication challenges, or retry ambiguous submissions automatically.
- Employer checks precede applicant data release. Runtime files and secrets stay out of source control.
- Start paused, require explicit configuration for automatic submission, and preserve exceptions without stalling independent work.

## Review Focus

- Cross-origin requests and uploaded filenames must not expose local credentials or files.
- Remote location restrictions and absent country authorization must not silently pass.
- An interrupted final submission must not turn into a duplicate retry.
- A trusted ATS hostname must not establish employer ownership by itself.
- Model output and website instructions must not invent facts or grant arbitrary tool access.

### Task 1: Domain policy and CV selection

Files: `backend/agent/models.py`, `matching.py`, `answers.py`, `security.py`, `tests/test_policy.py`.
Interfaces: Pydantic `Profile`, `Search`, `Settings`, `Job`, `Resume`; `match_job(job, search, profile)`, `select_resume(job, resumes, minimum)`, `resolve_answer(question, profile, resume)`.
- [x] Write tests for hard filters, geographic eligibility, aliases, salary units, CV ranking, and unknown factual answers.
- [x] Run `.venv/bin/python -m pytest tests/test_policy.py` and observe missing behavior.
- [x] Implement typed configuration, deterministic matching, CV ranking, and typed answer resolution.
- [x] Run policy tests; all pass.

### Task 2: Persistence and submission guard

Files: `backend/agent/store.py`, `worker.py`, `tests/test_store.py`.
Interfaces: `Store(path)`, persisted config/resumes/jobs/events, atomic queue claiming; `Worker(store, browser)` with `tick()`, `start()`, `stop()`.
- [x] Write tests for canonical deduplication, restart recovery, persistent pause, daily limits, and submission uncertainty.
- [x] Run store tests and observe missing behavior.
- [x] Implement SQLite transactions, durable queue, settings revision checks, recovery, and worker orchestration.
- [x] Run policy and persistence tests; all pass.

### Task 3: Browser and destination adapters

Files: `backend/agent/browser.py`, `forms.py`, `verification.py`, `model_gateway.py`, `tests/test_browser.py`, inline HTML fixtures.
Interfaces: `BrowserAgent.discover(search)`, `apply(job, profile, resume, before_submit)`, persistent login; adapter result with confirmation state. `verify_destination(job)` returns evidence. Browser actions remain predefined.
- [x] Write fixture tests for standard application fields, unknown questions, CV upload, confirmation, login challenges, and untrusted URLs.
- [x] Run tests and observe missing behavior.
- [x] Implement LinkedIn discovery/Easy Apply, Greenhouse/Lever/generic forms, employer checks, session reuse, keyring credentials, and bounded narrative model access.
- [x] Run tests; all pass. No live LinkedIn credentials are required.

### Task 4: Local API and upload boundary

Files: `backend/agent/api.py`, `main.py`, `resumes.py`, `tests/test_api.py`.
Interfaces: `/api/config`, `/api/resumes`, `/api/jobs`, `/api/events`, `/api/status`, `/api/control`, `/api/browser`, `/api/credentials`.
- [x] Write API tests for persistence, invalid config, origin protection, restricted file upload, queue control, and data deletion.
- [x] Run tests and observe missing behavior.
- [x] Implement localhost-only API, runtime permissions, resume extraction and versioning, browser control, and lifecycle integration.
- [x] Run the complete Python test suite; all pass.

### Task 5: Dashboard and local launcher

Files: `frontend/src/`, `frontend/package.json`, `scripts/setup.sh`, `scripts/start.sh`, `README.md`.
Interfaces: typed fetch client for Task 4 routes; dashboard, profile, searches, CV library, jobs, and settings views.
- [x] Implement the interface against the tested API, using real persisted state and visible empty states.
- [x] Build with `npm run build` and verify TypeScript checks.
- [x] Smoke-test setup, edits, uploads, queue controls, and navigation in a browser.
- [x] Document start/stop, credentials/model setup, adapter limits, and verification limitations.

### Task 6: Review and final verification

- [x] Run all Python tests, frontend checks/build, and browser fixture smoke tests.
- [x] Review security and submission behavior independently; fix material findings with regression tests.
- [x] Record actual live-integration limits and startup instructions. Do not claim live submissions have been tested.

## Execution record

Progress and deviations are recorded in `docs/superpowers/plans/2026-10-12-progress.md`.
This workspace initially has no Git repository, so work stays in the requested directory without worktree or commit operations.
