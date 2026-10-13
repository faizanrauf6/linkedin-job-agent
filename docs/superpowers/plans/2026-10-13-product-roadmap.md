# Practical Product Implementation Plan

> Follow test-driven development and execute verified increments in roadmap order.

**Goal:** Implement the authorized practical-product roadmap and publish each increment.
**Architecture:** Add durable domain modules and SQLite records around the existing
bounded application worker; expose working dashboard controls and capability evidence.
**Tech stack:** Python/FastAPI/Pydantic/SQLite/Playwright and React/TypeScript.
**Spec:** `docs/superpowers/specs/2026-10-13-product-roadmap-design.md`.

- [x] P0: Add checkpoint persistence, structured field details and restart-safe continuation; test browser loss, stale configuration/CV/options and uncertain outcomes.
- [ ] P1 portals: Integrate dedicated adapters, tenant guards and capabilities API/UI; verify complete supported journeys.
- [ ] P1 complex forms: Add record-aware fields, bounded repeating groups, dates, multi-select/conditional fields and confirmed additional uploads.
- [ ] P1 profile: Integrate structured applicant records, validation, answer resolution and accessible profile editors.
- [ ] P1 decisions: Persist and approve immutable payload snapshots; reject stale or changed submission payloads.
- [ ] P2 matching: Add explicit requirement interpretation and explainable weighted ranking with occupation-neutral fixtures.
- [ ] P2 documents: Add grounded reviewed drafts, bounded narrative composition and optional CV tailoring.
- [ ] P2 tracking: Add durable attempts/receipts, pipeline edits, exports and authorized read-only email evidence.
- [ ] P2 maintenance: Integrate CI/pinned setup, sanitized diagnostics, adapter health and validated backup/restore controls.
- [ ] Run full tests/build/smoke, review combined changes, resolve findings, publish and record exact results/limits.

Root owns shared integration files. Independent portal, structured-profile and
maintenance modules may be developed concurrently but are published as ordered
increments. Preserve current data, manual skips, held interventions and submission
reservation semantics. No force push, live account actions or real email access.
