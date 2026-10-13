# Practical application product

The user authorized implementing and pushing the roadmap in order. Keep the local
FastAPI/SQLite/Playwright/React architecture and additive database compatibility.

## Release increments and acceptance

1. **P0 reliability/intervention:** Retain existing seven fixes and multistep
   fixtures. Persist checkpoints and structured missing-field details in SQLite.
   Recover approved answers only for the same job, destination, configuration,
   CV hash and exact question/options/step signature. Reinspect every field and
   attachment after browser loss; never restore DOM handles or submission flags.
   Provide a clear continue action and preserve uncertain outcomes.
2. **P1 portals:** Dedicated Workday, iCIMS, Taleo, SmartRecruiters, Ashby,
   BambooHR and SuccessFactors adapters with capability metadata, exact tenant
   ownership and fixture journeys. Public capability status distinguishes fixtures
   from live certification. Account challenges and assessments remain intervention.
3. **P1 complex forms:** Bounded repeating employment/education records, typed
   dates/address fields, native and accessible multi-select controls, conditional
   required questions, confirmed additional documents and verified uploads.
4. **P1 profile:** Occupation-neutral structured records, certifications/licenses,
   languages, travel/shift preferences, typed salary and availability. Existing
   flat profile values continue to work; ambiguous facts remain unanswered.
5. **P1 decisions:** Persist immutable review snapshots of CV hash, answers and
   sources, documents, employer evidence and relevant configuration. Explicit
   snapshot approval must become stale on relevant changes. Verify current form
   answers against the approved snapshot before reserving submission in review mode.
6. **P2 matching:** Explain role/seniority interpretation and explicit mandatory
   requirements. Separate unknown requirements from confirmed incompatibility.
   Use weighted CV role/skill/requirement evidence without inventing credentials.
7. **P2 documents:** Grounded cover-letter drafts and length-aware narratives,
   plus optional reviewed CV-tailoring drafts composed only from existing facts.
   Generated drafts are never activated or attached without explicit review.
8. **P2 tracking:** Durable attempts and receipts, user-controlled pipeline stages,
   CSV/JSON export and optional read-only email evidence import. Email integration
   is opt-in and never sends messages or changes outcomes without confirmation.
9. **P2 maintenance:** CI, pinned installation, sanitized failure artifacts,
   adapter outcome metrics and validated backup/restore. Backups exclude browser
   sessions and credentials; restore remains paused and preserves uncertain sends.

## Verification and boundaries

Each increment adds behavioral tests, runs relevant fixtures and production build,
and is committed/pushed to the existing main branch only after verification.
Full suite and isolated dashboard smoke run at integration boundaries. Tests use
offline portals and fixture mail, never real applications or account creation.
Document supported journeys and completion metrics rather than claiming universal
automation. User signatures, new identity facts and assessments require intervention.
