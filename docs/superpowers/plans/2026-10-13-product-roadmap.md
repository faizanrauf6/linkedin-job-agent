# Practical Product Implementation Plan

> Follow test-driven development and execute verified increments in roadmap order.

**Goal:** Implement the authorized practical-product roadmap and publish each increment.
**Architecture:** Add durable domain modules and SQLite records around the existing
bounded application worker; expose working dashboard controls and capability evidence.
**Tech stack:** Python/FastAPI/Pydantic/SQLite/Playwright and React/TypeScript.
**Spec:** `docs/superpowers/specs/2026-10-13-product-roadmap-design.md`.

- [x] P0: Add checkpoint persistence, structured field details and restart-safe continuation; test browser loss, stale configuration/CV/options and uncertain outcomes.
- [x] P1 portals: Integrate dedicated adapters, tenant guards and capabilities API/UI; verify complete supported journeys.
- [x] P1 complex forms: Add record-aware fields, bounded repeating groups, dates, multi-select/conditional fields and confirmed additional uploads.
- [x] P1 profile: Integrate structured applicant records, validation, answer resolution and accessible profile editors.
- [x] P1 decisions: Persist and approve immutable payload snapshots; reject stale or changed submission payloads.
- [x] P2 matching: Add explicit requirement interpretation and explainable weighted ranking with occupation-neutral fixtures.
- [x] P2 documents: Add grounded reviewed drafts, bounded narrative composition and optional CV tailoring.
- [x] P2 tracking: Add durable attempts/receipts, pipeline edits, exports and authorized read-only email evidence.
- [x] P2 maintenance: Integrate CI/pinned setup, sanitized diagnostics, adapter health and validated backup/restore controls.
- [x] Run full tests/build/smoke, review combined changes, resolve findings, publish and record exact results/limits.

Root owns shared integration files. Independent portal, structured-profile and
maintenance modules may be developed concurrently but are published as ordered
increments. Preserve current data, manual skips, held interventions and submission
reservation semantics. No force push, live account actions or real email access.

## Release verification — 2026-10-13

- Complete offline Chromium/API/domain suite: **585 passed** (95.06 seconds).
- Production dashboard: TypeScript and Vite build passed.
- Expanded isolated dashboard smoke passed structured employment records, CV and
  supporting-document confirmation, named searches, start/pause, mobile layout,
  recovery/portal settings, backup download and default-disabled mailbox access.
- Independent integration review produced fixes for restored queued applications,
  arbitrary field names resembling paths, concurrent data deletion and receipt
  locations after iframe submission. Targeted regression checks passed.
- Runtime data, browser sessions, CV originals and credentials remain ignored by Git.
- Each roadmap increment was published to the authorized repository main branch.

These are offline fixture results, not live vendor certifications. No real
application, account creation, mailbox connection or external message was made.
IMAP supports explicit manual app-password sync of up to 20 recent messages;
OAuth-only mailboxes require the manual EML fallback. Custom assessments,
signatures, verification challenges and unsupported portal controls require input.
See `docs/practical-product.md` and `docs/portal-support.md` for workflows and limits.
