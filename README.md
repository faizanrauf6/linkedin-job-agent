<div align="center">

# Orbit

**Your local workspace for job applications.**

Discover roles, match your CVs, review answers, and track what happens next.

[![Checks](https://github.com/faizanrauf6/linkendin-job-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/faizanrauf6/linkendin-job-agent/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-TypeScript-149ECA?logo=react&logoColor=white)
![Playwright](https://img.shields.io/badge/Browser-Playwright-2EAD33)

[Get started](#quick-start) · [User guide](docs/practical-product.md) · [Portal support](docs/portal-support.md) · [Maintenance](docs/maintenance.md)

</div>

---

## Quick start

**Requires:** Python 3.11+, Node.js 20.19+ or 22.12+, and npm. Saving credentials requires a working OS keyring.

```sh
git clone https://github.com/faizanrauf6/linkendin-job-agent.git
cd linkendin-job-agent
./scripts/setup.sh
./scripts/start.sh
```

Open **[localhost:8765](http://127.0.0.1:8765)**. Stop with `Ctrl+C`; your workspace survives restarts. Keep your computer awake while processing.

## From profile to application

1. **Set up** — Save your profile, upload CVs and supporting documents, and confirm their accuracy.
2. **Discover** — Create searches, sign in manually through the dedicated browser, and select **Search now**.
3. **Review** — Use **Prepare without submitting**, check the exact answers and attachments, then approve the snapshot in review mode.
4. **Run & track** — Start the agent. Complete requested input, inspect receipts, and update recruiter stages as you go.

Automatic mode queues eligible matches without per-job approval. Use review mode when you want to approve each application.

## What you get

| | Features |
| --- | --- |
| **Discovery & matching** | Named searches, saved progress, mandatory-requirement checks, and weighted CV selection |
| **Profiles & forms** | Employment, education, licenses, languages, repeating records, and additional documents |
| **Review & recovery** | Exact approval snapshots, durable checkpoints, and explicit continuation after your input |
| **Written documents** | Grounded cover-letter and CV drafts with source review |
| **Tracking** | Attempts, observed receipts, recruiter stages, CSV/JSON exports, and opt-in read-only email |
| **Local operations** | Pause controls, limits, private backup/restore, sanitized diagnostics, and adapter metrics |

**Portal adapters:** LinkedIn Easy Apply, Greenhouse, Lever, Workday, iCIMS, Taleo, SmartRecruiters, Ashby, BambooHR, and SuccessFactors. See the [capability matrix](docs/portal-support.md) for supported journeys.

## Know the boundaries

Portal coverage is **fixture-tested, not live-certified**. Custom forms, assessments, signatures, CAPTCHA, and MFA may require your input. Ambiguous submissions wait for reconciliation before retrying.

LinkedIn prohibits unauthorized automation and may restrict accounts. Employer checks reduce risk but cannot guarantee a listing is legitimate.

Workspace data stays in `.runtime/`. Applicant data reaches application sites when you apply; optional cloud models and email connections require explicit configuration. Keep runtime files and backups private, and keep the API on loopback.

## Documentation

- [User guide](docs/practical-product.md) — Profiles, applications, continuation, documents, and tracking
- [Portal support](docs/portal-support.md) — Capabilities, fixture evidence, and limitations
- [Configuration](docs/configuration.md) — Models, credentials, account permissions, and live checks
- [Maintenance](docs/maintenance.md) — Pinned installation, backup/restore, and diagnostics
- [Architecture & roadmap](docs/superpowers/specs/2026-10-13-product-roadmap-design.md) — Design and implementation scope

<details>
<summary><strong>Development & verification</strong></summary>

```sh
.venv/bin/python -m pytest
npm --prefix frontend run build
# Stop the running server before this isolated dashboard check.
.venv/bin/python scripts/smoke.py
```

For frontend development, run `./scripts/start.sh` and `npm --prefix frontend run dev` in separate terminals. CI runs the fixture suite, production build, and dashboard smoke check.

</details>
