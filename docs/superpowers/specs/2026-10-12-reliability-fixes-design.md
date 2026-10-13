# Application reliability fixes

The user explicitly requested implementation of the seven reviewed defects. Work proceeds under that authorization, preserving local data and existing submission safeguards.

1. Classify editable, review, and confirmation application steps separately. Review-only steps remain actionable; only new visible confirmation after a reserved submission counts as submitted.
2. Keep one canonical job and separate evaluations per search. Any enabled matching search may qualify the job. Reevaluate unsubmitted jobs on relevant configuration changes without reviving manually skipped jobs or overwriting active/confirmed/uncertain applications.
3. Persist discovery progress per search definition, rotate title/location combinations fairly, skip already inspected listings without consuming the new-job budget, and bound pages on every run.
4. Preserve existing values that match confirmed facts. Unknown required values need explicit confirmation tied to job, field and current value. Continue the retained verified application document when possible; invalidate continuation on configuration, CV or document changes. Restart safely after browser loss. Never accept personal attestations automatically.
5. Traverse employer-linked supported ATS boards within bounded depth/page budgets, recording provenance. Render JavaScript careers evidence in an isolated browser context with public-address request/redirect restrictions and no applicant cookies or data. An ATS host alone never proves ownership.
6. Keep credentials restricted to their exact HTTPS host. After supported successful login, destroy retained credential documents before restoring the verified application host set and reopening a safe GET destination.
7. Select and verify existing CVs only with adapter-specific strong identity evidence; filenames alone are insufficient to establish document content. Replace unverifiable attachments when upload is available; otherwise require intervention.

Acceptance: meaningful regressions for each defect, existing Python suite, frontend build, isolated dashboard smoke. No live application submissions, account creation, OS startup activation, or real credentials in tests.
