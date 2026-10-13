# Local maintenance

Run `./scripts/setup.sh` to install Python dependencies from `requirements.lock`, install the local package without resolving another dependency set, install Chromium, and build the dashboard using `npm ci`. Python 3.11 or later and a supported Node version (20.19+, 22.12+, or newer) are required. CI repeats the pinned dependency install, Chromium install, dashboard build, and complete pytest suite on Ubuntu with Python 3.11 and Node 20. Tests use local fixtures; no application accounts or repository secrets are configured.

## Backups and restore

The maintenance API exports a version 1 ZIP containing `agent.db`, referenced document files, and `manifest.json`. SQLite's backup API captures committed WAL data consistently. Documents use portable runtime-relative paths. Every payload file has its size and SHA-256 digest in the manifest. Archive files are created with owner-only permissions.

Only referenced PDF, DOCX, TXT, RTF, and ODT files inside the runtime are eligible. Browser profiles, cookies, keychain files, credential files, external document paths, and symlinks are excluded. OS keychain credentials are separate from the runtime and are never queried by backup or restore. The database includes applicant information, CV text, saved answers, events, checkpoints, and outcome history: keep archives private. Hashes detect corruption; they do not authenticate an archive supplied by someone else.

Pause the agent and wait for all browser and worker operations to finish before backup or restore. The caller must enforce that state. Write backup archives outside the runtime. `create_backup(runtime, destination)` accepts an explicit archive path or an existing destination directory. Paths must not contain symlinks. `restore_backup(runtime, archive)` restores validated records and documents, and the caller must reload its Store afterwards.

Restore checks archive version, names, entry types, duplicate entries, compression ratio, expanded size, file hashes, database integrity, configuration and job records, document references, and CV hashes in staging before changing the runtime. Limits are 256 MB for an archive, 512 MB expanded, 256 MB for one file, and 2,000 entries. Unsafe or unlisted entries are rejected. Existing document destinations and SQLite sidecars must be regular files. Recoverable installation errors roll back replaced files; restore is not a transaction across an operating-system crash, so retain the prior backup until review is complete.

Restored settings are paused. Queued jobs return to awaiting review with an application hold and invalidated approval. An interrupted application becomes `needs_input`, or `submission_uncertain` if submission had already started. No restored interrupted or uncertain application is queued automatically. Submitted records, timestamps, outcome history, and checkpoints remain available for reconciliation. Review each pending application and deliberately queue or continue it before resuming. Restoring does not import browser sessions or OS credentials.

## Adapter diagnostics

`record_adapter_result` stores aggregate outcome counts by adapter and version, with bounded error-class counts. `adapter_health` reads these counts for the UI. These records contain no applicant values or page HTML.

`sanitized_failure` produces a shareable form-shape record containing the job ID, adapter/version, error class, state, step, labels, field types, and option labels. It excludes answer values and arbitrary exception text, redacts known answer values and common contact/credential patterns, and uses a fixed short reason for each outcome. Common field labels and option labels remain readable; unrecognized portal text becomes a digest placeholder because it could contain applicant information. The artifact never exports the job description, CV text, raw page HTML, credentials, URLs, or answer maps.
