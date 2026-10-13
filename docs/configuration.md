# Configuration and live validation

[← Back to README](../README.md)

## Models and credentials

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

