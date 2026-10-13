# LinkedIn personal application agent — proposed design

Date: 2026-10-12
Status: Proposed for user review; implementation has not started.

## Goal and agreed constraints

Build a personal agent that finds LinkedIn jobs matching the user's skills,
positions, and technology stacks and submits matching applications using saved
profile information. Filters and operating settings must be configurable. The
user has expanded the requested scope to include employer websites reached from
LinkedIn, automatic screening answers, login handling, suspicious-job filtering,
and automatic selection among two or three supplied CVs.

The user has selected LinkedIn as the only job-discovery source and running on
their own computer first. Employer application destinations are now in scope;
independent discovery from other job boards is not.
The current workspace is empty. The design below proposes the initial scope
and technical choices; those choices have not yet been approved.

Continuous operation means operation while the computer is awake, connected,
and the application is running. Sleep, shutdown, expired login, verification,
LinkedIn restrictions, and website changes can interrupt operation.

## LinkedIn integration constraint

LinkedIn prohibits unauthorized website automation and warns that accounts may
be restricted or closed. Apply Connect is a recruiting-system integration, not
a general personal application-submission interface. This design therefore
proposes browser automation with that limitation explicitly visible during
setup. It does not promise reliable or unrestricted 24/7 submission.

References:
- https://www.linkedin.com/help/linkedin/answer/a1341387/prohibited-software-and-extensions?lang=en
- https://learn.microsoft.com/en-us/linkedin/talent/apply-connect/apply-connect-overview

## Application scope and practical limits

- Discover jobs through LinkedIn's job search using the signed-in browser.
- Check employer identity and application destinations before transmitting any
  resume, contact details, or other applicant data.
- Evaluate job details against the configured profile and search rules, then
  choose the best-fitting CV from the user's supplied library.
- Submit supported LinkedIn Easy Apply applications and supported employer
  applications reached from LinkedIn.
- Initially provide dedicated adapters for Greenhouse and Lever public
  application forms, plus bounded generic form handling for standard visible
  HTML fields. An ATS name alone does not establish that every tenant or form
  is supported. Unsupported custom portals are recorded explicitly.
- Answer supported screening questions from verified profile facts, user
  preferences, and grounded narrative generation when a model is configured.
- Reuse sessions and automate ordinary supported login forms with explicitly
  configured credentials. Do not bypass CAPTCHA, MFA, or access controls.
- Save exceptional work for intervention without blocking independent jobs.
  A LinkedIn authentication failure pauses LinkedIn discovery and applications;
  an employer portal failure pauses only work needing that portal.
- Do not promise that every website is supported, every job is legitimate, or
  every question or verification can be resolved without the user.

## User experience

A dashboard available only on the local computer provides:

1. Setup: save the applicant profile, upload two or three CVs, confirm extracted
   facts, and open a dedicated browser to sign into LinkedIn initially.
2. Search configuration: create, edit, enable, and disable named searches.
3. Matched jobs: inspect the job, matching reasons, employer checks, selected
   CV, proposed or used answers, missing information, and application status.
4. Application settings: choose review mode or automatic submission, configure
   CV selection, saved answers, employer portal sessions, model access, limits,
   and pause or resume the agent.
5. Activity: view discovery runs, submissions, failures, and items needing
   attention. A submission count includes only confirmed submissions.

Setup begins paused. Automatic submission is available after the user saves
their profile and explicitly selects it. Review mode is also configurable.

## Configuration

### Applicant profile

- Name, contact details, location, profile links, and a CV library.
- Work history, education, target positions, seniority, skills, technology
  stacks, and years of experience.
- Work authorization and sponsorship answers by country.
- Availability, salary expectations with currency and pay period, and saved
  screening answers.

Profile information and answers are supplied and editable by the user. Fields
absent from the profile stay unknown; they are not inferred as facts.

### CV library and automatic selection

- Accept PDF and DOCX files, retain the original files, and extract text locally.
- Give each CV a label, intended positions, seniority, skills, and optional
  user-defined priorities. Support the requested two or three CVs without making
  three an artificial system limit.
- Detect extraction failures and conflicting personal facts before activation.
  Image-only files require readable text or user-supplied metadata; OCR is not
  included in the initial version.
- Rank CVs against the actual job description using role and technology fit,
  supported experience, and user priorities. Select the highest eligible score
  above the configured minimum; use an explicit user priority to break ties.
- Require intervention when no CV clears the minimum, rather than silently
  uploading an unrelated default. Preserve the ranking and selection reason.
- Upload the selected original document and verify the chosen filename or
  upload indicator before final submission. Do not rewrite qualifications or
  silently generate a new CV.
- Bind every application to an immutable CV version and content hash so later
  uploads cannot change the document recorded for an earlier application.

### Screening question automation

- Prefer exact saved answers and structured facts for factual questions such as
  experience, location, availability, salary, and country-specific authorization.
- Map paraphrases to a supported question type and validate units and choices.
  Do not infer total technology experience from overlapping employment dates.
- Generate role-specific narrative answers from confirmed work history, the
  selected CV, and job details when a configured language model is available.
  Keep evidence references internally and reject unsupported claims.
- Validate every answer against field type, available options, required status,
  length limits, and the applicable country or role.
- Optional unanswered fields may be omitted. Required fields without reliable
  facts stay pending. A model's confidence is not evidence for a personal fact.
- Save recurring answers and preferences to reduce future interruptions.
  Do not generalize company-specific answers or generated prose indiscriminately.
- Voluntary demographic questions use an explicit saved preference or an
  available decline-to-answer choice; never infer sensitive characteristics.
- Assessments, coding tests, signatures, and attestations requiring the applicant
  personally to act remain outside autonomous completion.

### Employer and suspicious-job checks

- Keep job suitability and suspected-fraud decisions separate. Record evidence
  and reasons, not a claim that a risk score proves authenticity.
- Inspect LinkedIn company and poster context, verification signals when
  available, the company's independent careers presence, and consistency of
  title, location, requisition, and employer identity.
- Confirm external destinations belong to the employer or an ATS tenant
  referenced by that employer's careers site. A familiar ATS host or HTTPS
  alone is insufficient. Recheck every redirect before releasing applicant data.
- Reject payment, cryptocurrency, bank-login, and unusual sensitive-document
  requests during application. Flag impersonation, mismatched domains, and
  implausible promises. Lack of a verification badge alone is not a rejection.
- Classify outcomes as eligible, blocked, or unresolved. Automatic submission
  requires positive employer/destination evidence and no blocking signals.
  Unresolved listings remain visible but do not receive applicant data.
- Even these checks cannot guarantee a real vacancy, an active hiring process,
  or a trustworthy employer. Expose their evidence and limitations in the UI.

Guidance informing these checks:
- https://consumer.ftc.gov/articles/job-scams
- https://www.linkedin.com/help/linkedin/answer/a9535030
- https://www.linkedin.com/help/linkedin/answer/a1497886

### Sessions and login automation

- Reuse per-site authenticated sessions in the dedicated browser profile.
- After initial setup, fill supported ordinary login forms only with credentials
  the user explicitly configured for that exact site. Store credential secrets
  in the operating system credential store, not SQLite or model prompts.
- Never reuse LinkedIn credentials on an employer site or an unverified domain.
- Attempt reauthentication with bounded retries; avoid repeated failed logins.
- New account registration is disabled unless the user explicitly enables it
  for a supported, verified portal. Store any generated password securely.
- CAPTCHA, unfamiliar verification, MFA approval, and account restrictions may
  still require the user. Show the browser at the blocked step and resume after
  successful verification. Do not assume access to the user's email or SMS.
- Defer the affected portal's work and continue unrelated jobs when possible.

### Search and matching rules

- Included job titles and excluded title keywords.
- Required skills and preferred skills, including editable technology aliases.
- Countries and cities, with multiple named searches when necessary.
- Remote, hybrid, and onsite arrangements.
- Seniority and employment types.
- Salary range, currency, and pay period.
- Work authorization, sponsorship, and relocation preferences.
- Included or excluded companies, posting age, and Easy Apply availability.
- Minimum match score and configurable handling of missing job information.

The distinction between remote and geographic eligibility is explicit. A
remote posting does not automatically qualify for every country. Ambiguous
eligibility or mandatory requirements follow the configured missing-information
policy: reject or require review, never silently pass.

Hard requirements are evaluated before ranking. A configurable score ranks
remaining jobs using title, seniority, and preferred skill matches. Each result
includes an explanation. Deterministic filters and CV ranking work without an
external AI service. A configurable language-model component supports narrative
answers and semantic interpretation; it cannot override hard requirements or
employer checks. Model access can be local or through an explicitly selected
cloud provider. Without a configured model, narrative-only applications remain
pending while factual and saved-answer applications can continue.

Salary is compared only when currency and pay period are compatible. Missing
or incompatible salary data follows the missing-information policy; automatic
currency conversion is outside the initial scope.

### Operating rules

- Search interval and maximum jobs inspected per run.
- Submission cap per day and optional operating hours with a selected timezone.
- Review or automatic submission mode.
- Automatic CV-selection thresholds, priorities, and saved screening answers.
- Supported employer destinations, site-specific login settings, and model
  provider settings with explicit control over cloud data sharing.
- Start, pause, resume, and stop controls.

Operating limits control the user's workload and application volume; they do
not guarantee protection against LinkedIn restrictions.

## Proposed architecture

- Frontend: React and TypeScript, built with Vite, for the local dashboard.
- Backend: Python FastAPI for configuration, status, file handling, and control.
- Storage: SQLite for profiles, settings, jobs, application state, and events.
- Browser: Playwright with a dedicated persistent browser profile. It does not
  use or modify the user's everyday browser profile.
- Application adapters: LinkedIn Easy Apply, Greenhouse, Lever, and a bounded
  generic form adapter, each reporting supported fields and confirmation rules.
- Matching and CV selector: deterministic constraints, documented ranking, and
  optional semantic model support with recorded reasons.
- Answer engine: typed saved-fact resolution, optional grounded text generation,
  and independent validation before browser actions.
- Employer verifier: destination checks and evidence collection that run before
  any applicant data is transmitted.
- Model gateway: a configurable local or cloud text-model endpoint. Credentials
  are kept in the OS credential store; no provider is silently selected.
- Worker: one local backend-managed worker processing a durable SQLite queue.
  A process lock prevents concurrent agents from using the same session.
- Startup: a documented local launcher starts the backend, worker, and dashboard.
  An explicit stop cleanly stops the worker and browser.

Adapters, verification, CV selection, answers, matching, queue, and API have
separate interfaces. Website-specific selectors stay in adapters so website
changes can be addressed without rewriting matching or storage code. Browser
actions are bounded operations, not arbitrary model-generated code execution.

## Data flow and application state

Search schedule → LinkedIn discovery → job normalization and deduplication →
employer and destination checks → hard filters and ranking → CV selection →
answer planning → review or application queue → supported Easy Apply or
external form completion → confirmed submission or an actionable pending state.

Jobs are deduplicated by LinkedIn job ID, plus canonical employer requisition
and application URL when available. A unique application record per canonical
job prevents repeated submissions through different LinkedIn listings or routes.
Ambiguous duplicate identity is deferred for reconciliation before submission.

Application states are: discovered, rejected, ready, awaiting_review, queued,
applying, needs_input, blocked, submitted, failed, and submission_uncertain.
Records retain transition reasons, configuration revision, selected CV version,
answer provenance, employer evidence, and adapter version used.

Only explicit, adapter-validated LinkedIn or employer confirmation permits a
submitted state. Clicking a button, uploading a CV, or reaching a generic page
does not count as confirmation. If the process
stops or the result is ambiguous after submission begins, the application is
marked submission_uncertain and is not automatically resubmitted. The user
must reconcile it with the destination before retrying.

The worker rechecks pause state, submission limits, and current configuration
before starting an application and immediately before final submission. It also
rechecks employer eligibility, destination trust, CV version, and answer validity.
Changing filters causes queued applications to be reevaluated. Pausing stops
new actions after any browser action already in progress completes; it cannot
undo a submission already sent.

## Recovery and intervention

- Retry transient discovery or navigation errors with bounded backoff.
- Never automatically retry a final submission whose outcome is unknown.
- Attempt saved-fact resolution and grounded generation for new questions before
  requiring intervention. Missing personal facts still require the user.
- Attempt session restoration and supported configured reauthentication before
  deferring a login. Expose verification challenges and a resume action.
- Pause the affected adapter or portal for restrictions or changed page structure;
  continue other eligible work when the shared LinkedIn session is unaffected.
- For unresolved questions, preserve the application record and show the exact
  question. User-supplied reusable answers can unblock compatible pending jobs.
- Recover persisted pending work after restart; reconcile interrupted
  applications before continuing.
- Show the last successful run and next scheduled run so an idle worker is
  distinguishable from a functioning agent with no matching jobs.

## Local data protection

- Bind the dashboard and backend to loopback, not the network interface.
- Validate request origins and protect state-changing requests against cross-site
  requests. The local browser session is not a public API.
- Keep the SQLite database, resumes, and browser session in an ignored runtime
  directory with restricted filesystem permissions.
- Initial LinkedIn sign-in happens in the browser. Any optional credentials for
  subsequent supported login automation are stored only in the OS credential
  store and scoped to the exact verified site.
- Treat browser cookies as credentials. Do not write cookies, resume contents,
  contact details, or screening answers to diagnostic logs.
- Use generated filenames for uploads, restrict resume types and sizes, and
  keep uploaded files outside directly served frontend assets.
- Provide controls to disconnect the browser session and remove local data.
- Treat job descriptions and website text as untrusted content. They cannot
  change agent settings, execute code, access arbitrary local files, extract
  credentials, or instruct the model to transmit data to unrelated destinations.
- Block browser automation navigation and requests to loopback, private network,
  link-local, and non-HTTP(S) destinations. Validate redirects and normalized
  domain ownership; do not trust substring matches or userinfo in URLs.
- Transmit only application-relevant data to verified destinations. Cloud model
  use is explicit opt-in with visible data scope; do not send credentials or
  demographic answers to a model. Local model inference is configurable.
- OS disk encryption is recommended for protection at rest; the first version
  does not claim application-level database encryption.

## Verification and acceptance

- Unit tests exercise hard filters, missing-information behavior, salary units,
  ranking, geographic eligibility, cross-route duplicate prevention, CV selection,
  factual answer provenance, and unsupported narrative claims.
- Queue tests exercise restart recovery, limits, pause behavior, configuration
  changes, and uncertain submissions.
- Browser-adapter tests use local fixtures for multi-step applications,
  Easy Apply and external forms, CV uploads, screening questions, ordinary login,
  verification challenges, redirects, and confirmation handling.
- Employer-verification tests cover legitimate ATS tenants, employer-domain
  mismatches, unavailable evidence, private network destinations, payment
  requests, and attempts to inject instructions into job or website text.
- Model-gateway tests use a fake provider for missing facts, malformed answers,
  provider failure, and data-sharing restrictions; tests require no paid API.
- API tests cover configuration persistence, local request protections, and
  upload boundaries.
- A local smoke check verifies setup, filter editing, job review, queue controls,
  and activity reporting.
- Live LinkedIn verification requires the user's signed-in session. Any live
  test that submits an application must be deliberately initiated by the user;
  automated tests never submit real applications.

The first version is accepted when a user can configure their profile and
searches, discover and explain matching jobs, inspect employer-check evidence,
automatically choose among supplied CVs, resolve supported screening questions,
complete supported Easy Apply and employer forms in the selected mode, reuse
configured login sessions, inspect confirmed outcomes, resolve exceptional work,
and resume without duplicate submissions.

## Deferred scope

Cloud hosting, operating while the computer is off, independent discovery from
other job boards, guaranteed support for every employer portal, CAPTCHA/MFA
bypass, recruiter messaging, automatic CV rewriting, coding assessments, and
multi-user accounts are not part of this version. Paid cloud AI is optional and
requires a provider explicitly configured by the user.
