# Orbit Completion Implementation Plan

> Use test-driven development and parallel agents for independent browser, evidence, and operations work; root owns shared models, API/UI integration and verification.

**Goal:** Close the eleven requested implementation gaps and provide a reviewable live-validation flow.
**Architecture:** Additive configuration and persistent state; bounded browser adapters; explicit account and host scope.
**Tech Stack:** Existing Python/FastAPI/SQLite/Playwright/React/TypeScript.
**Spec:** docs/superpowers/specs/2026-10-12-completion-design.md

## Global constraints

Keep existing data compatible. No challenge bypass, guessed qualifications, arbitrary SSO host trust or automatic retries after a submission starts. No real external submissions during tests.

## Review focus

Untrusted iframe/SSO destinations; repeated result pages; stale CV conflict acknowledgements; retries after ambiguous submission; startup command injection and private credential handling.

## Tasks

1. Shared schema: additive fields for pagination/recovery, aliases, relocation, CV conflicts, portal scope, adapter evidence and sleep prevention. Check legacy round trips and invalid limits.
2. Browser/adapters: fixture tests for pagination/deduplication, embedded portal forms, widgets, signup gating, session checks and dry-run preparation. Implement bounded behavior and verify fixtures.
3. CV/answers/matching: tests for explicit fact conflicts, education/relocation, custom aliases, selected CV narrative grounding and ungrounded model output. Implement and verify.
4. Operations: tests for durable retries/portal isolation/uncertain submission, inspectable macOS startup configuration, sleep-process lifecycle and dependency compatibility. Implement and verify.
5. API/dashboard integration: expose controls, conflict acknowledgement, portal resume, non-submitting live checks and adapter audit fields; test API guards and build UI.
6. Final validation: full suite, build, isolated browser smoke, fresh independent review, corrections, README and completion ledger. Restart local app and verify health. Report actual account-dependent validation separately.

## Progress

- Planning complete; explicit user request authorizes implementation. No Git repository exists, so changes remain in this workspace without branch/commit operations.
- Shared interfaces assigned before dispatch. Agents own separate production files; root owns models.py, api.py, frontend and integration tests.
