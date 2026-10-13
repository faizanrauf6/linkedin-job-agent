# Orbit — local LinkedIn application agent

A personal dashboard and persistent browser worker that discovers LinkedIn jobs,
matches your filters, chooses among your CVs, and processes supported applications.
Runs on your computer; it cannot work while the computer is asleep or off.

## Run

Requires Python 3.11+ and Node.js 20.19+ (or 22.12+). On macOS, credentials use
Keychain. Linux requires a working keyring backend if you save credentials.

```sh
./scripts/setup.sh
./scripts/start.sh
```

Open **http://127.0.0.1:8765**. Stop with Ctrl+C. The database and browser profile
survive restarts. If your network cannot resolve `files.pythonhosted.org`, configure
a trusted PyPI mirror for setup using `PIP_INDEX_URL`; no mirror is hardcoded.

1. Complete **My profile**, including country-specific authorization and experience.
2. Upload your CV variants in **CV library**, inspect extracted text, add tags, and
   confirm accuracy. Each upload is an immutable document version.
3. Create **Searches & filters** for your roles, stacks, locations, and preferences.
4. Open **LinkedIn** from Overview and sign in in the dedicated Chromium browser.
5. Optionally add exact company names and official domains in **Settings** to help
   corroborate employer careers links. This does not bypass listing checks.
6. Use **Search now** while paused. Prepare an opportunity, inspect its exact
   application review snapshot, then approve it to queue in review mode.
7. Choose review or automatic mode, save, and click **Start agent**. In automatic
   mode, eligible new matches are queued without per-job approval. Start explicitly
   enables processing queued applications in either mode.

See the [practical product guide](docs/practical-product.md) for structured profiles,
repeating forms, exact approval snapshots, written documents, tracking, opt-in email,
and backup/restore. The [portal capability matrix](docs/portal-support.md) lists
fixture-tested journeys and their limits.

## What is implemented

- Local React dashboard with profile, CV library, named searches, opportunities,
  screening answers, settings, application outcomes, and activity.
- SQLite state, cross-listing URL deduplication, atomic queue claims, limits,
  pause controls, operating hours, and restart recovery.
- Independent evaluations of each vacancy against each search, with automatic
  reevaluation of eligible unsubmitted jobs after settings or CV changes.
- Persistent discovery cursors and rotation across title/location combinations;
  previously inspected listings do not consume the new-candidate limit.
- Hard job filters and explainable CV ranking. Missing mandatory data does not
  silently qualify a job; CVs must be confirmed before selection.
- LinkedIn browser search and Easy Apply flow, plus public employer HTML forms
  including Greenhouse, Lever, Workday, iCIMS, Taleo, SmartRecruiters, Ashby,
  BambooHR and SuccessFactors. Adapters use supported semantic fields and
  step controls, not arbitrary model-generated browser scripts.
- Structured employment, education, licenses, certifications, languages, salary
  and availability; supported repeating records, conditional questions, dates,
  multi-selects and confirmed supporting-document uploads.
- Immutable approval snapshots bind the selected CV, answers, documents, settings
  and employer evidence to the exact submission payload.
- Grounded reviewed cover letters and optional CV drafts preserve original CVs;
  narrative limits stop incomplete answers.
- Durable attempts and observed receipts, separate recruiter stages, JSON/CSV
  exports, authorized email imports and explicit read-only IMAP sync.
- Private validated backup/restore, sanitized diagnostics, adapter measurements
  and CI using pinned dependency installation.
- Exact saved screening answers, typed profile answers, resume upload checks,
  and optional model-assisted selection of confirmed narrative evidence.
- Editable, review-only and confirmation step detection; supported saved CV
  selection requires matching document bytes against the selected CV hash.
- Explicit confirmation of manually completed fields and continuation of the
  retained application, with fresh question, value and attachment checks.
- Employer website/careers corroboration, suspicious-payment checks, public URL
  checks, and restricted request destinations while applicant data is present.
- Saved browser sessions and optional exact-host credentials in the OS keyring.
  Successful login destroys documents retaining credentials before restoring
  verified application hosts and reopening the destination with a safe GET.
- A submission is counted as submitted only after visible confirmation.
  Interrupted or ambiguous sends become **Check outcome**, never an automatic
  retry. Reconcile the outcome in the destination portal before retrying.

## Integration limits

**This is an initial implementation, not a guarantee of universal application
coverage or live LinkedIn compatibility.** LinkedIn and employer sites change.
The tests exercise local fixtures, not a signed-in LinkedIn account; no real
application was submitted during development.

Job discovery paginates LinkedIn search results and scrolls supported lazy lists.
It stops at your configured page/job caps, empty pages, or repeated result pages.
Page cursors and uninspected page remainders survive restarts. Search-definition
changes reset the relevant cursor. Unavailable detail pages are recorded and
acknowledged so they cannot indefinitely block later candidates.
It does not promise exhaustive discovery beyond those bounds.
Location and salary metadata absent from a supported page stay unknown.
Salary extraction requires an explicit currency and pay period; ambiguous dollar
amounts and mixed currencies stay unknown. Sponsorship is inferred only from
supported explicit statements. Greenhouse, Lever, and generic adapters support standard fields and selected
accessible custom widgets. Embedded forms require a verified allowed host.
Multi-domain sign-in requires an explicit per-portal host allowlist. Nonstandard
forms may still need an adapter; live tenant compatibility is not certified.
Coding assessments, signatures, and personal attestations require your action.

CAPTCHA, MFA approvals, new-device checks, and account restrictions can require
your intervention. Unknown required personal facts remain pending. The worker
continues unrelated jobs where possible. For employer websites that need manual
sign-in, open the browser at that portal, complete sign-in, then queue the job
again. Do not manually submit a form while the worker is also processing it.

For missing answers, pause the agent and complete supported fields in its browser.
Open the opportunity, choose **Review filled browser answers**, inspect the values,
then choose **Confirm answers & queue continuation** and start the agent. A retained
queued application is processed before discovery can navigate away from it.
Confirmation is specific to the application document, question, current value,
settings and CV. Changing those invalidates it. Browser restarts and navigation
can destroy the retained form. Durable checkpoints preserve unambiguous confirmed
answers for explicit continuation; navigation may replay prior portal steps. Changed
settings, documents, or questions require a fresh review.
Prepared applications and applications awaiting manual input remain held until
you explicitly queue them. Saving settings does not silently resume them.

Saved CV reuse is supported only for recognized LinkedIn, Greenhouse and Lever
controls with downloadable content matching the selected CV's SHA-256. Filename
alone is insufficient. When verification fails, an available upload control is
used to replace the document; otherwise the application needs attention.

Employer checks reduce risk; they cannot establish that every listing is real,
actively hiring, or scam-free. Listings without corroborating careers evidence
remain pending. Do not treat an ATS domain or LinkedIn badge as proof. LinkedIn
prohibits unauthorized automation and can restrict accounts; local execution and
daily limits do not remove that risk.

Employer verification can follow an official website to a supported ATS company
board and then the exact vacancy, retaining each ownership transition. Traversal
is bounded and confined to the linked tenant. JavaScript careers evidence is
rendered in a separate disposable browser without applicant cookies or data;
it does not grant arbitrary embedded hosts access to the application session.

## Written answers and credentials

Factual answers work without a model. Configure an OpenAI-compatible `/v1`
endpoint and model name in Settings for narrative evidence selection. Local
example: `http://127.0.0.1:11434/v1` with a separately installed local model.
Hosted endpoints require HTTPS and **Allow cloud model requests** enabled.
Save a provider key separately after saving the endpoint.

The model receives job context, confirmed summary/work-history facts, and
supported evidence from the selected confirmed CV after contact-data redaction.
It selects evidence IDs; the app composes constrained role-specific answers from
those exact facts and retains the evidence sources. It rejects invented facts. Unsupported or malformed responses stay
pending. Resume ranking is deterministic and does not depend on model access.

Portal credentials are optional and specific to the exact hostname. They are
used only for supported ordinary login forms after destination verification.
Initial LinkedIn sign-in is manual. Credentials are never returned by the API.
Disconnect clears the browser profile; clearing local data does not delete
separately stored OS keychain entries. Remove those using your OS credential
manager (services start with `linkedin-local-agent:`).

## Completion controls

Settings now includes pagination limits, bounded retry attempts and exponential
backoff, portal cooldowns, and editable technology aliases. Only transient failures
before submission are retried. Uncertain submissions never retry automatically.
Suspended portals are visible separately so unrelated work can continue.

CV imports extract explicit identity facts and compare them with your profile and
other CVs. Conflicts block activation until you explicitly acknowledge the
differences. New identity evidence invalidates the acknowledgement. Extraction is
conservative; an unlabeled or ambiguous fact may still require manual review.
Education answers support explicit labeled Degree, Institution, Field of study,
and Graduation year lines. Relocation preferences are country-specific and never
override an explicit current-residence requirement.

Account creation requires **Allow account creation** plus the exact portal in
**Approved account creation portals**. Supported ordinary signup forms use a
generated password saved in the OS credential store before transmission. Email
verification, CAPTCHA/MFA and personal agreements remain intervention points.
Preparing an application never creates an account.

Additional sign-in/form hosts must be configured per exact portal. Listing a host
allows applicant-data requests to it after employer verification, so include only
services the employer actually uses. No host is inferred from an arbitrary iframe
or sign-in link.

On macOS, **Enable login startup** installs the launchd configuration generated in
`.runtime/com.orbit.local-agent.plist`. **Disable login startup** removes it.
This is optional and is not activated during setup or tests. **Keep computer awake**
uses caffeinate while the agent is enabled within operating hours; it cannot keep
the agent running through shutdown or a closed laptop. Other platforms report
these OS controls as unsupported.

Credentials require HTTPS and an exact-host form action. Account creation records
an attempted signup before sending it, so an interrupted signup is not repeated
automatically. Inspect the account or sign in to reconcile that outcome. Visible
recruiter names and verification signals are recorded as context, never as proof
that a vacancy is legitimate.

## Live validation

1. Pause the agent. Open LinkedIn from the dashboard and sign in manually.
2. In Settings, use **Check signed-in session**. Its timestamped result is
   separate from fixture test results.
3. Configure a profile, confirmed CV and search, then use **Search now**.
4. Inspect a matching opportunity and use **Prepare without submitting**.
   This verifies/fills a supported form and stops before final submission.
   The opportunity records the adapter name/version and any missing questions.
5. Check the form and employer destination before choosing to queue a real
   application. Real submissions are counted only after visible confirmation
   or your explicit manual outcome reconciliation.

The session/preparation checks never submit applications or create accounts.
No signed-in live account or real application was used in automated development
tests; account-dependent compatibility requires these live checks.

## Development and verification

```sh
.venv/bin/python -m pytest
npm --prefix frontend run build
# Stop the running agent first; this starts its own isolated temporary server.
.venv/bin/python scripts/smoke.py
```

For frontend development, run the API with `./scripts/start.sh` and Vite in a
second terminal with `npm --prefix frontend run dev`. Both bind to loopback.
The production launcher serves the frontend and API on the same port.

Runtime files live in `.runtime/` and are ignored by Git. Use OS disk encryption
for protection at rest. Keep this directory and your browser cookies private.
Do not expose the local API through a tunnel or a public reverse proxy.

See `docs/superpowers/specs/2026-10-12-linkedin-local-agent-design.md` for the
design and `docs/superpowers/plans/2026-10-12-local-agent.md` for the build plan.
