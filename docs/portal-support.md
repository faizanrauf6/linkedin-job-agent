# Portal support and verification scope

Portal adapters are **fixture verified, not live certified**. The seven additional
portals below have offline synthetic HTML fixtures executed in real Chromium
through `BrowserAgent`. These fixtures use explicit application containers,
semantic fields, English action labels, and browser events. They are not captured
vendor pages, a promise that current vendor markup matches, or evidence that an
application has been submitted to any employer.

`agent.portal_capabilities.capability_registry()` returns JSON-safe records with
`name`, `adapter_version`, `status`, `live_certified`, `supported_scopes`,
`limitations`, and `evidence`. Each adapter exposes the matching `capability`.
Generic forms are `best_effort`; named adapters are `fixture_verified`.

| Adapter | Recognized fixture container examples | Additional action labels | Ownership boundary |
| --- | --- | --- | --- |
| Workday | `[data-automation-id="applyFlowPage"]` | Save and Continue | Exact `*.myworkdayjobs.com` host and career-site path, with optional locale prefix |
| iCIMS | `#iCIMS_ApplicationForm`, `form#iCIMS_MainForm` | Standard Continue / Submit | Exact tenant subdomain on `icims.com`, under `/jobs` |
| Taleo | `#requisitionDescriptionInterface` | Apply Online; Save and Continue | Exact tenant host on `taleo.net` plus `/careersection/<site>` |
| SmartRecruiters | `[data-test="application-form"]`, `form#application-form` | Standard Next / Submit application | Exact `jobs.smartrecruiters.com` or `careers.smartrecruiters.com` host plus company path |
| Ashby | `.ashby-application-form-container` | Standard Review / Submit Application | Exact `jobs.ashbyhq.com` host plus company path |
| BambooHR | `#applicationForm`, `form[data-testid="application-form"]` | Standard Continue / Submit application | Exact tenant subdomain on `bamboohr.com`, under `/careers` |
| SuccessFactors | `#jobApplication`, `form[name="application"]` | Save and Continue; final Apply | Exact `career[digits].successfactors.com` or `.eu` host and one `company` query parameter on `/career` |

Each new adapter also accepts a visible `form` or `role="form"` explicitly labeled
`Application`. Recognized portal hosts do not fall back to arbitrary forms:
a newsletter form must not receive applicant information. Existing LinkedIn,
Greenhouse, Lever, and generic adapters retain their original scopes. Custom
vanity domains use the generic adapter unless separately implemented.

## What the fixtures exercise

For every new adapter, `tests/test_portal_coverage.py` runs a three-stage journey:

1. Fill grounded first-name/email fields and upload the selected CV.
2. Advance using the portal's supported action label, preserve attachment proof
   when the prior DOM is removed, fill last name, and reach review.
3. Stop during dry run without invoking the submission gate. Continue the same
   retained document, invoke the explicit gate once, submit, and require a new
   visible confirmation.

The tests retain production form filling, attachment verification, continuation,
and submission behavior. They replace external ownership/network evidence with
an eligible fixture and intercept every browser request. No credentials, real
accounts, or live applications are used. Separate ownership tests run the real
`verify_destination` traversal against supplied HTML: official employer → careers
page → portal board → exact vacancy. A linked board cannot corroborate another
tenant or career site, even when it shares the hostname. Entry tests also reject
cross-tenant Apply links and ignore unrelated newsletter forms.

A known portal hostname alone establishes no employer ownership. Tenant parsing
rejects missing or ambiguous company identifiers, encoded path separators and
traversal, and retains exact hostname/region. New path/query tenant identifiers
are case-exact; no undocumented case equivalence is assumed. Employer evidence
and explicit existing SSO settings remain the authority for permitted hosts;
recognition adds no credential or registration hosts.

## Limits

Account creation, CAPTCHA, MFA, employer-specific assessments, changed/custom
markup, and unsupported localized button labels are outside these adapter
fixtures. Existing common account and challenge handling still applies, but these
records do not certify those flows. Unknown required questions or unsupported
steps stop for input; a portal name must not imply complete automation.
SuccessFactors' final `Apply` is actionable only within its supported application
scope and after verified attachment, review/dry-run, and submission checks.

Live certification would require separately authorized, dated employer-specific
validation and recorded UI variants. Until then, support remains narrowly bounded
by the offline journeys above. The adapter version is diagnostic, not a vendor UI
version or certification identifier.
