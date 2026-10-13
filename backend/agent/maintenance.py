"""Local maintenance with portable documents and deliberately value-free diagnostics."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .models import Config, Job, Resume, now

ARCHIVE_VERSION = 1
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 2000
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt", ".rtf", ".odt"}
PRIVATE_PARTS = {"browser", "cookies", "keychain", "keychains", "credentials", "secrets", "sessions", ".aws", ".ssh", ".env"}
STATES = ("submitted", "needs_input", "failed", "submission_uncertain", "blocked")
SAFE_LABELS = {"name", "full name", "first name", "last name", "email", "email address", "phone", "phone number", "address", "location", "country", "city", "state", "postal code", "resume", "cv", "cover letter", "linkedin", "linkedin profile", "portfolio", "website", "work authorization", "visa sponsorship", "sponsorship", "salary", "salary expectation", "salary expectations", "availability", "notice period", "years of experience", "education", "degree", "gender", "ethnicity", "race", "disability", "veteran status", "pronouns", "consent", "signature", "preferred name", "current company", "current title"}
SAFE_OPTIONS = {"yes", "no", "true", "false", "other", "prefer not to say", "decline to answer", "i do not wish to answer", "please select", "select an option", "male", "female", "non-binary", "remote", "hybrid", "onsite", "on-site", "full-time", "part-time", "contract", "internship"}


def _root(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Maintenance roots must not contain symlinks")
    return path


def _name(value: str) -> str:
    path = PurePosixPath(value)
    if not value or "\\" in value or "\x00" in value or ":" in value or path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ValueError("Archive contains an unsafe path")
    if any(part.lower() in PRIVATE_PARTS or any(term in part.lower() for term in ("cookie", "credential", "keychain")) for part in path.parts):
        raise ValueError("Private session or credential files are excluded")
    if value not in {"agent.db", "manifest.json"} and path.suffix.lower() not in DOCUMENT_EXTENSIONS:
        raise ValueError("Archive contains an unsupported document")
    return value


def _confined(root: Path, name: str) -> Path:
    path = root / _name(name)
    if any(p.is_symlink() for p in (path, *path.parents) if p == root or root in p.parents):
        raise ValueError("Document paths must not contain symlinks")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Document path escapes runtime")
    return path


def _json_rows(db):
    """Find data columns without coupling backups to checkpoint/history schemas."""
    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        table = row[0]
        quoted = '"' + table.replace('"', '""') + '"'
        columns = db.execute(f"PRAGMA table_info({quoted})").fetchall()
        if any(c[1] == "data" for c in columns):
            for record in db.execute(f"SELECT rowid,data FROM {quoted}").fetchall():
                try:
                    data = json.loads(record[1])
                except (ValueError, TypeError):
                    raise ValueError("Backup database contains invalid record data") from None
                yield quoted, record[0], data


def _paths(value, transform, table):
    # Only schema-owned document references are portable filesystem paths.
    # Arbitrary applicant answer keys and portal field names remain plain data.
    if table in {'"resumes"', '"documents"'} and isinstance(value, dict):
        value = dict(value)
        if isinstance(value.get("path"), str) and value["path"]:
            value["path"] = transform(value["path"])
    return value


def _validate_database(db):
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Backup database failed integrity validation")
    config = db.execute("SELECT data FROM config WHERE id=1").fetchone()
    if config is None:
        raise ValueError("Backup has no application configuration")
    Config.model_validate_json(config[0])
    for row in db.execute("SELECT id,status,data FROM jobs"):
        job = Job.model_validate_json(row[2])
        if job.id != row[0] or job.status != row[1]:
            raise ValueError("Backup job identity or status is inconsistent")
    for row in db.execute("SELECT id,data FROM resumes"):
        resume = Resume.model_validate_json(row[1])
        if resume.id != row[0]:
            raise ValueError("Backup CV identity is inconsistent")


def create_backup(runtime: Path, destination: Path) -> Path:
    """Write a versioned ZIP using SQLite's online consistent backup API.

    Only the database and referenced document files are included. Browser state,
    keychain entries and unreferenced runtime files are never copied.
    """
    runtime = _root(runtime)
    database = _confined(runtime, "agent.db")
    if not database.is_file():
        raise ValueError("Runtime database does not exist")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = runtime / ("agent.db" + suffix)
        if sidecar.is_symlink() or sidecar.exists() and not sidecar.is_file():
            raise ValueError("Database sidecars must be regular files inside runtime")
    destination = _root(destination)
    if destination.is_dir():
        destination /= "agent-backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".zip"
    if destination == database or destination.is_relative_to(runtime):
        raise ValueError("Store the backup outside the runtime directory")
    if not destination.parent.is_dir():
        raise ValueError("Backup destination directory does not exist")
    with tempfile.TemporaryDirectory(prefix="agent-backup-") as temporary:
        snapshot = Path(temporary) / "agent.db"
        documents = {}
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(snapshot) as db:
            source.backup(db)
            _validate_database(db)

            def portable(raw):
                original = Path(raw)
                if not original.is_absolute():
                    original = runtime / original
                if not original.is_relative_to(runtime):
                    raise ValueError("A referenced document is outside the runtime")
                name = original.relative_to(runtime).as_posix()
                path = _confined(runtime, name)
                if name in {"agent.db", "manifest.json"} or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError("A referenced document is missing or oversized")
                data = path.read_bytes()
                if name in documents and documents[name] != data:
                    raise ValueError("A document changed while backing up")
                documents[name] = data
                return name

            for table, rowid, data in list(_json_rows(db)):
                db.execute(f"UPDATE {table} SET data=? WHERE rowid=?", (json.dumps(_paths(data, portable, table)), rowid))
            for row in db.execute("SELECT data FROM resumes"):
                resume = Resume.model_validate_json(row[0])
                if resume.path and resume.sha256 and hashlib.sha256(documents[resume.path]).hexdigest() != resume.sha256:
                    raise ValueError("A CV changed since it was imported")
            db.commit()
            db.execute("PRAGMA journal_mode=DELETE")
        payload = {"agent.db": snapshot.read_bytes(), **documents}
        if len(payload) + 1 > MAX_ENTRIES or sum(map(len, payload.values())) > MAX_EXPANDED_BYTES:
            raise ValueError("Backup exceeds the supported size")
        manifest = {"version": ARCHIVE_VERSION, "created_at": now(), "files": {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in payload.items()}}
        fd, name = tempfile.mkstemp(prefix=".agent-backup-", dir=destination.parent)
        os.close(fd)
        pending = Path(name)
        try:
            with zipfile.ZipFile(pending, "w", zipfile.ZIP_STORED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                for name, data in payload.items():
                    archive.writestr(name, data)
            if pending.stat().st_size > MAX_ARCHIVE_BYTES:
                raise ValueError("Backup exceeds the supported size")
            pending.chmod(0o600)
            os.replace(pending, destination)
        finally:
            pending.unlink(missing_ok=True)
    return destination


def _read_archive(archive: Path) -> dict[str, bytes]:
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Backup archive exceeds the supported size")
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > MAX_ENTRIES or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
            raise ValueError("Backup expands beyond the supported size")
        names = set()
        for entry in entries:
            _name(entry.filename)
            mode = entry.external_attr >> 16
            if entry.filename in names or entry.is_dir() or stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in {0, stat.S_IFREG} or entry.flag_bits & 1:
                raise ValueError("Backup contains duplicate, encrypted or special entries")
            if entry.file_size > MAX_FILE_BYTES or entry.file_size > 1024 * 1024 and entry.file_size / max(entry.compress_size, 1) > 200:
                raise ValueError("Backup contains an oversized or highly compressed entry")
            names.add(entry.filename)
        if "manifest.json" not in names or "agent.db" not in names:
            raise ValueError("Backup manifest or database is missing")
        if source.getinfo("manifest.json").file_size > 1024 * 1024:
            raise ValueError("Backup manifest exceeds the supported size")
        manifest = json.loads(source.read("manifest.json"))
        if manifest.get("version") != ARCHIVE_VERSION or not isinstance(manifest.get("files"), dict):
            raise ValueError("Unsupported backup manifest version")
        if set(manifest["files"]) != names - {"manifest.json"}:
            raise ValueError("Backup entries do not match its manifest")
        payload = {}
        for name, expected in manifest["files"].items():
            data = source.read(name)
            if expected.get("size") != len(data) or expected.get("sha256") != hashlib.sha256(data).hexdigest():
                raise ValueError("Backup file failed hash validation")
            payload[name] = data
        return payload


def restore_backup(runtime: Path, archive: Path) -> dict:
    """Validate in staging, then replace documents and database while paused.

    The caller must stop browser/worker activity and reload its Store afterwards.
    Restored queued jobs receive application holds; interrupted submissions never
    pass through Store.recover's automatic retry path.
    """
    runtime = _root(runtime)
    archive = _root(archive)
    try:
        payload = _read_archive(archive)
        # Check every destination before any mutation of runtime.
        destinations = {name: _confined(runtime, name) for name in payload}
        sidecars = [runtime / ("agent.db" + suffix) for suffix in ("-wal", "-shm", "-journal")]
        for path in [*destinations.values(), *sidecars]:
            if path.is_symlink() or path.exists() and not path.is_file():
                raise ValueError("Restore destinations must be regular files")
        with tempfile.TemporaryDirectory(prefix="agent-restore-") as temporary:
            staging = Path(temporary)
            database = staging / "agent.db"
            database.write_bytes(payload["agent.db"])
            with sqlite3.connect(database) as db:
                _validate_database(db)
                referenced = set()

                def local(raw):
                    name = _name(raw)
                    if name in {"agent.db", "manifest.json"} or name not in payload:
                        raise ValueError("Backup refers to a missing document")
                    referenced.add(name)
                    return str(destinations[name])

                for table, rowid, data in list(_json_rows(db)):
                    db.execute(f"UPDATE {table} SET data=? WHERE rowid=?", (json.dumps(_paths(data, local, table)), rowid))
                if referenced != set(payload) - {"agent.db"}:
                    raise ValueError("Backup includes unreferenced documents")
                for row in db.execute("SELECT data FROM resumes"):
                    resume = Resume.model_validate_json(row[0])
                    if resume.path and resume.sha256:
                        name = Path(resume.path).relative_to(runtime).as_posix()
                        if hashlib.sha256(payload[name]).hexdigest() != resume.sha256:
                            raise ValueError("Restored CV does not match its saved hash")
                config = Config.model_validate_json(db.execute("SELECT data FROM config WHERE id=1").fetchone()[0])
                config.settings.paused = True
                config.revision += 1
                db.execute("UPDATE config SET data=? WHERE id=1", (config.model_dump_json(),))
                db.execute("CREATE TABLE IF NOT EXISTS application_holds (job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE)")
                for row in db.execute("SELECT data FROM jobs").fetchall():
                    job = Job.model_validate_json(row[0])
                    if job.status == "applying":
                        job.status = "submission_uncertain" if job.submission_started else "needs_input"
                        job.error = "Restored interrupted application; reconcile before continuing"
                    if job.submission_started and job.status == "queued":
                        job.status = "submission_uncertain"
                    if job.status == "queued":
                        job.status = "awaiting_review"
                        job.error = "Restored application; review before continuing"
                    if not job.submission_started and job.status in {"awaiting_review", "needs_input", "discovered", "rejected"}:
                        job.approved_revision = -1
                        job.updated_at = now()
                    if job.status in {"awaiting_review", "needs_input", "submission_uncertain"}:
                        db.execute("INSERT OR IGNORE INTO application_holds VALUES (?)", (job.id,))
                    job.retry_at = ""
                    db.execute("UPDATE jobs SET status=?,data=? WHERE id=?", (job.status, job.model_dump_json(), job.id))
                db.commit()
                db.execute("PRAGMA journal_mode=DELETE")
            payload["agent.db"] = database.read_bytes()
            # Fully stage bytes before installing any destination. Roll back on IO errors.
            runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
            rollback = {}
            installed = []
            old_sidecars = {path: path.read_bytes() if path.exists() else None for path in sidecars}
            try:
                for name in [n for n in payload if n != "agent.db"] + ["agent.db"]:
                    destination = destinations[name]
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    rollback[name] = destination.read_bytes() if destination.exists() else None
                    fd, pending_name = tempfile.mkstemp(prefix=".restore-", dir=destination.parent)
                    try:
                        with os.fdopen(fd, "wb") as pending:
                            pending.write(payload[name])
                            pending.flush()
                            os.fsync(pending.fileno())
                        os.replace(pending_name, destination)
                        installed.append(name)
                    finally:
                        Path(pending_name).unlink(missing_ok=True)
                # Idle Store connections are closed by its context manager. Remove
                # old sidecars only after installation succeeds to avoid stale WAL.
                for sidecar in sidecars:
                    sidecar.unlink(missing_ok=True)
            except BaseException:
                for name in reversed(installed):
                    destination = destinations[name]
                    if rollback[name] is None:
                        destination.unlink(missing_ok=True)
                    else:
                        destination.write_bytes(rollback[name])
                for sidecar, original in old_sidecars.items():
                    if original is not None:
                        sidecar.write_bytes(original)
                raise
        return {"restored": True, "documents": len(payload) - 1, "paused": True, "message": "Restored applications require review before continuing"}
    except (sqlite3.Error, zipfile.BadZipFile, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid backup archive or database") from exc


def _token(value, fallback="", limit=80):
    value = str(value or "")
    return value[:limit] if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value) else fallback


def sanitized_failure(job, result=None, missing_details=None) -> dict:
    """Export form shape only; arbitrary failure text and answer values stay local."""
    def get(value, name, default=""):
        return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)

    secrets = [str(v) for value in (job, result) if value is not None for v in get(value, "answers", {}).values() if str(v)]

    def label(value, *, option=False):
        value = str(value or "")
        if "<" in value or ">" in value:
            return "[redacted]"
        for secret in sorted(secrets, key=len, reverse=True):
            value = re.sub(re.escape(secret), "[redacted]", value, flags=re.I)
        value = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\+?\d[\d ()-]{7,}\d", "[redacted]", value)
        value = re.sub(r"(?i)\b(?:password|token|secret|api[_ -]?key|cookie)\s*[:=]\s*\S+", "[redacted]", value)
        value = " ".join(value.split())[:160]
        normalized = value.replace("[redacted]", "").strip().rstrip("*:?").strip().casefold()
        if normalized in (SAFE_OPTIONS if option else SAFE_LABELS) or value == "[redacted]":
            return value
        # Unknown portal text could itself contain an applicant's name or CV.
        # Keep a stable identifier for support without exporting that text.
        kind = "option" if option else "label"
        return f"[custom {kind} {hashlib.sha256(value.encode()).hexdigest()[:12]}]" if value else ""

    state = get(result, "state", get(job, "status"))
    reasons = {"needs_input": "Application fields or portal state require review", "failed": "Application interaction failed before confirmation", "blocked": "Application was blocked by a safety check", "submission_uncertain": "Submission outcome is uncertain; reconcile before retry", "submitted": "Application confirmation was observed"}
    fields = []
    details = missing_details or [{"label": q} for q in get(result, "questions", get(job, "questions", []))]
    if isinstance(details, dict):
        details = details.get("fields", [])
    for item in list(details)[:40]:
        if isinstance(item, str):
            item = {"label": item}
        kind = get(item, "type")
        kinds = {"text", "textarea", "email", "tel", "url", "number", "date", "select", "select-one", "select-multiple", "combobox", "radio", "checkbox", "password", "file", "hidden"}
        fields.append({"label": label(get(item, "label")), "type": kind if kind in kinds else "unknown", "options": [label(option, option=True) for option in list(get(item, "options", []))[:40]]})
    step = get(missing_details, "step", "review")
    if step not in {"editable", "review", "confirmation", "entry", "verification", "upload", "submit"}:
        step = "review"
    return {"job_id": _token(get(job, "id")), "adapter": _token(get(job, "adapter_name"), "unknown"), "version": _token(get(job, "adapter_version"), "unknown"), "error_class": _token(get(job, "last_error_class"), "ApplicationFailure"), "step": step, "state": state if state in STATES else "needs_input", "reason": reasons.get(state, "Application requires review"), "fields": fields}


def record_adapter_result(store, job, state, error_class="") -> None:
    """Persist bounded aggregate counts; no applicant/form data is recorded."""
    if state not in STATES:
        raise ValueError("Unsupported adapter outcome")
    adapter = _token(job.adapter_name, "unknown")
    version = _token(job.adapter_version, "unknown")
    error_class = _token(error_class)
    key = f"adapter_health:{adapter}:{version}"
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        value = json.loads(row[0]) if row else {"adapter": adapter, "version": version, "attempts": 0, **{s: 0 for s in STATES}, "errors": {}}
        value["attempts"] += 1
        value[state] += 1
        if error_class:
            errors = value["errors"]
            bucket = error_class if error_class in errors or len(errors) < 40 else "OtherError"
            errors[bucket] = errors.get(bucket, 0) + 1
        value["last_state"] = state
        value["updated_at"] = now()
        db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))


def adapter_health(store) -> list[dict]:
    with store.connection() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT value FROM meta WHERE key LIKE 'adapter_health:%' ORDER BY key")]
