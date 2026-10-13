# Orbit completion design

Implement the eleven gaps requested on 2026-10-12 in the existing local FastAPI/SQLite/Playwright/React application. The user explicitly requested implementation now; proceed with reversible local work without another design approval cycle.

## Architecture and behavior

- Preserve existing saved data through additive model defaults. Backend models own configuration; browser adapters own website behavior; the worker owns durable scheduling and submission state.
- Paginate LinkedIn results with stable job-ID deduplication, lazy-list scrolling, empty/repeated-page termination, and configurable page/job caps. Never claim exhaustive discovery beyond those limits.
- Add Greenhouse, Lever and generic form adapters, including supported embedded frames and common widget controls. Restrict additional SSO hosts to explicit per-portal configuration; never guess allowed hosts from page content.
- Account creation is opt-in per exact verified portal, uses generated passwords saved in OS Keychain before transmission, stops on email verification/MFA/CAPTCHA, and never accepts personal attestations automatically.
- Retry only transient pre-submission errors, with bounded exponential backoff. Persist per-portal suspension and retry timing; other portals continue. Submission uncertainty is never automatically retried.
- Extract explicit CV identity facts and compare against profile/other CVs. Conflicts block confirmation until explicitly acknowledged; absence is unknown, not a conflict. Do not log raw facts.
- Add explicit education mappings, relocation preferences, user-editable technology aliases, adapter/version audit fields and selected-CV evidence for narrative answers. Narrative claims must remain grounded in confirmed evidence, with source IDs retained.
- Expand employer warnings and collect visible recruiter/verification context; a badge never establishes trust by itself.
- Provide local non-submitting session/application checks and record actual live validation separately from fixtures. Real submissions require a selected job and explicit final approval.
- Add optional macOS login startup and sleep prevention controls. Generate inspectable launch-agent configuration; activate only on explicit control use. Retain loopback binding and pause state.
- Resolve TestClient dependency deprecation without suppressing warnings. Test compatibility and record dependency versions.

## Acceptance

Meaningful failing tests precede changes. Full Python suite, frontend build, and isolated browser smoke must pass. No automated test submits real applications or creates real accounts. Clearly document unsupported website behavior and account-dependent validation that cannot be completed without a signed-in user.
