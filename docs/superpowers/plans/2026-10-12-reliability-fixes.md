# Application Reliability Implementation Plan

> **For agentic workers:** Follow test-driven development and verify each task before integration.

**Goal:** Fix all seven reviewed application reliability defects.

**Architecture:** Preserve the canonical job queue while adding independent search evaluations and persistent search cursors. Extend the bounded browser adapter flow with review-step detection, validated continuation, attachment verification and safe post-login transitions. Improve employer evidence traversal without widening applicant-data trust implicitly.

**Tech Stack:** Python/FastAPI/SQLite/Playwright, React/TypeScript.

**Spec:** `docs/superpowers/specs/2026-10-12-reliability-fixes-design.md`

## Global Constraints

- Preserve existing databases through additive schema/model changes.
- Never retry uncertain submissions automatically.
- No real applications, accounts or credentials in verification.
- Keep credential and applicant-data destination restrictions.
- Workspace has no Git metadata; edit current authorized workspace and do not create commits.

## Review Focus

- A manually skipped application must remain skipped after settings changes.
- Disabling one search must not suppress a job eligible under another enabled search.
- Continuation approvals must not survive changed field values, CV versions or application documents.
- Employer-linked ATS traversal must not trust a different tenant on the same shared host.
- Post-login restoration must destroy parent, sibling and popup documents retaining secrets.

## Task 1: Search evaluations

Files: `backend/agent/store.py`, `backend/agent/worker.py`, new `tests/test_search_reliability.py`.

- [x] Write and run failing overlapping-search/config-reevaluation/manual-skip regressions.
- [x] Add durable per-search evaluations separate from canonical identity; evaluate enabled searches and preserve terminal/manual states.
- [x] Run focused queue/policy/worker tests.

## Task 2: Discovery progress

Files: new `backend/agent/discovery.py`, new `tests/test_discovery_reliability.py`; coordinator integrates `browser.py` and worker/store interfaces.

- [x] Write and run failing rotation/known-ID/progress/reset regressions.
- [x] Implement persistent bounded cursor and fair query scheduling.
- [x] Integrate browser discovery with storage progress and known IDs; preserve simple fixture compatibility.

## Task 3: Employer evidence

Files: `backend/agent/verification.py`, new `tests/test_verification_reliability.py`; coordinator integrates isolated browser renderer.

- [x] Write and run failing employer-to-ATS-board and tenant-boundary regressions.
- [x] Implement bounded provenance-preserving crawl and optional rendered evidence callback.
- [x] Integrate isolated public-only rendering with no applicant state.

## Task 4: Application steps, login and saved CVs

Files: `backend/agent/adapters.py`, `backend/agent/browser.py`, new `tests/test_application_reliability.py`.

- [x] Write and run failing review-only, safe login restoration and saved-CV identity regressions.
- [x] Implement explicit step classification, safe document destruction/restoration and adapter-specific attachment selection.
- [x] Run browser/security/registration fixtures.

## Task 5: Prefilled fields and continuation

Files: `backend/agent/forms.py`, `backend/agent/browser.py`, `backend/agent/api.py`, `frontend/src/Jobs.tsx`, new continuation regressions.

- [x] Write and run failing current-value/confirmation/stale-approval regressions.
- [x] Validate prefilled facts and add explicit browser continuation confirmation with document-bound values.
- [x] Expose confirmation action in job details and preserve final-submission reservation.

## Task 6: Integration and review

- [x] Run full Python suite, frontend production build and dashboard smoke.
- [x] Obtain fresh code review; resolve material findings and rerun affected checks.
- [x] Update README and progress record with behavior and remaining live-validation limits.

## Completion evidence — 2026-10-12

All seven requested fixes are implemented. New SQLite tables and metadata are
additive; canonical vacancies remain separate from search-specific decisions.
Fresh code review identified continuation, attachment and cancellation races.
Each reproduced finding received a regression and correction before the final run.

Final verification:

- Full Python suite: **258 passed in 61.71 seconds**.
- Frontend: `npm --prefix frontend run build` passed TypeScript and Vite checks.
- Isolated dashboard smoke passed with no browser errors or mobile overflow.
- Application and verification browser checks used offline fixtures. No real
  account creation, credentials or application submissions were used.

Manual continuation is in memory and tied to the retained application document.
Browser loss or substantive configuration/CV changes requires preparation again.
Saved CV reuse requires recognized controls and matching downloaded content;
unsupported controls fall back to a fresh upload or manual attention. Live
LinkedIn and employer compatibility still requires signed-in validation.
