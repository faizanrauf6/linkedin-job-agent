# Practical guide to Orbit

Orbit runs on your computer. Begin with the agent paused, and keep the computer
awake while it is working. The dashboard is at `http://127.0.0.1:8765` after
`./scripts/start.sh`.

## Set up your facts and searches

1. In **My profile**, enter accurate contact details, summary, skills, and
   country-specific authorization. Add separate employment, education,
   certification, license, and language records. Keep dates, issuers, identifiers,
   salary, availability, and work preferences explicit. Missing information stays
   unknown; a job requirement does not become a fact about you.
2. In **CV library**, upload each original PDF or DOCX variant, inspect its
   extracted text, and confirm accuracy. Review any identity conflicts before
   acknowledging them. Changed conflict evidence requires a new acknowledgement.
3. Add supporting documents, such as licenses or certificates, separately from
   CVs. Inspect and explicitly confirm each original. Ambiguous attachment labels
   require your choice; a supporting document does not replace the selected CV.
4. Create and name searches in **Searches & filters**. Configure titles, locations,
   workplace, required qualifications, and missing-information policy for each.
   Use **Search now** while paused to inspect matches and their explanations.
   Search and page limits bound discovery; results are not exhaustive.

Sign into LinkedIn manually in the dedicated agent browser. Employer checks must
corroborate the application destination through official careers evidence. An ATS
hostname or a visible badge alone establishes no employer ownership. Exact trusted
employer domains can help supply that evidence.

The capability list covers LinkedIn, Greenhouse, Lever, Workday, iCIMS, Taleo,
SmartRecruiters, Ashby, BambooHR, and SuccessFactors. Named adapters are verified
against offline fixtures; live employer compatibility is uncertified. Custom
markup and tenant differences can require manual work. See
[portal support and tested scopes](portal-support.md).

## Prepare, complete, review, and start

Use review mode for a deliberate first application:

1. Open an opportunity and choose **Prepare without submitting**. Inspect the
   selected CV, employer evidence, filled answers, and any missing questions.
   Preparation stops before final submission.
2. Complete missing supported answers through **Continue after my input** and
   explicitly confirm them. For answers filled directly in the live browser,
   choose **Review filled browser answers**, inspect the current values, then
   **Confirm answers & queue continuation**. Personal verification and ambiguous
   questions remain manual.
3. In **Review this application**, inspect the exact destination, CV fingerprint,
   additional documents, answers, sources, and application settings. Check the
   approval box and choose **Approve and queue**.
4. Choose **Start agent**. Review mode checks the approved payload again before
   submission. Changes to the destination, profile/settings, answers, sources,
   CV, or selected documents can require fresh review. Queueing alone does not
   authorize a changed payload.

Durable checkpoints retain recorded questions and confirmed answers. After browser
loss, recovery may replay earlier UI steps and reinspect the form and attachments;
it cannot guarantee restoration of the original portal screen. Do not submit
manually while the worker is processing the same application.

Automatic mode can queue eligible matches without individual application approval.
It still enforces confirmed facts and attachments, employer evidence, operating
hours, daily limits, and uncertainty guards. An uncertain submission requires
outcome reconciliation before retrying; it is not retried automatically.

Supported journeys use ordinary semantic/native forms and recognized accessible
controls. Processing is bounded to 10 repeated records per supported section,
200 fields per inspection, and 20 application steps. Unknown complex widgets,
signatures, personal attestations, assessments, CAPTCHA, MFA, and other verification
steps require your action. These bounds do not imply universal form coverage.

## Written documents

In an opportunity's **Written documents**, choose a cover letter or CV tailoring
draft and a character budget. The draft selects or reorders supplied profile facts
and supported confirmed CV evidence. Inspect the unchanged text and **Facts and
sources**, check that you reviewed them, and choose **Confirm reviewed draft** before
**Download reviewed DOCX**.

Source or configuration changes invalidate review/export until you create and
review a fresh draft. CV tailoring creates a separate document: it never overwrites
the original CV or automatically changes the CV selected for the application.

## Track outcomes and optional email evidence

**Application history & next steps** separates processing attempts, observed
browser submission receipts, and recruiter pipeline stages. A tracking stage
change does not queue, submit, or reconcile an application outcome. Export history
as CSV or JSON when needed.

Import an `.eml` file only after giving explicit consent to store its text with
that opportunity. Email text and sender identity remain unverified evidence.
Matching employer/role references can suggest a recruiter stage; inspect and
explicitly accept it. Imported email is not an observed browser receipt.

Optional mailbox sync requires explicit opt-in, an exact IMAP hostname, TLS, and
an app password stored separately in the OS credential store. Each manual sync
inspects at most 20 recent messages and imports only employer-and-role matches.
The mailbox is opened read-only: sync does not change read flags, send email,
change submission outcomes, or automatically accept pipeline suggestions. Providers
that require OAuth instead of app passwords use the manual `.eml` fallback.

## Maintenance and release checks

In **Settings**, pause the agent and wait for current work to finish before backup
or restore. Keep downloaded archives private: they contain applicant records,
documents, answers, checkpoints, and history. Cookies/browser sessions and OS
keyring credentials are excluded. Restore validates the archive, keeps the agent
paused, and holds queued applications for deliberate review. Reconcile interrupted
or uncertain submissions before continuing.

Sanitized diagnostics omit answer values, raw page HTML, CV text, and credentials.
Recorded adapter completion ratios describe observed attempts in this workspace;
they do not certify live compatibility or predict future success. See
[maintenance and restore details](maintenance.md).

Run release validation from the repository root:

```sh
.venv/bin/python -m pytest
npm --prefix frontend run build
# Stop the running server first; smoke uses an isolated temporary server.
.venv/bin/python scripts/smoke.py
```

Automated development validation uses offline fixtures and performs no real
applications. For separate signed-in manual validation, keep the agent paused,
sign in yourself, run **Check signed-in session**, inspect a bounded **Search now**
result, and use **Prepare without submitting**. Record the portal/adapter version,
observed fields, and limitations. For a real review-mode submission, deliberately
approve that exact application and start the agent.
