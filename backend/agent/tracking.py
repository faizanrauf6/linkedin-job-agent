"""Durable application history and explicitly imported, untrusted email evidence."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from urllib.parse import urlsplit, urlunsplit

from .models import Job, now, uid
from .security import public_url

STAGES = ("prospect", "applied", "interview", "offer", "accepted", "rejected", "withdrawn", "archived")
ATTEMPT_STATES = {"submitted", "needs_input", "failed", "submission_uncertain", "blocked", "queued", "applying", "awaiting_review", "rejected"}
MAX_EMAIL_BYTES = 1024 * 1024
MAX_EMAIL_TEXT = 10000


def _get(value, key, default=""):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _text(value, limit=500):
    # Store text, never active HTML or control characters. UI renders it as text.
    value = re.sub(r"<[^>]*>", "", str(value or ""))
    return "".join(character for character in value if character in "\n\t" or ord(character) >= 32)[:limit]


def _csv_cell(value):
    value = str("" if value is None else value)
    if re.sub(r"^[\s\ufeff]*", "", value).startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def _receipt(value):
    if not isinstance(value, dict):
        return None
    text = _text(value.get("text") or value.get("confirmation_text"), 1000)
    reference = _text(value.get("reference"), 160)
    if not text and not reference:
        return None
    url = str(value.get("url") or "")
    if len(url) > 2000 or not public_url(url, resolve=False):
        url = ""
    else:
        parsed = urlsplit(url)
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return {"text": text, "reference": reference, "url": url, "observed_at": _text(value.get("observed_at"), 80) or now()}


def _email(raw: bytes, job: Job):
    if not raw or len(raw) > MAX_EMAIL_BYTES:
        raise ValueError("Import a nonempty email within the 1 MB size limit")
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    if not parsed.get("From") or not parsed.get("Subject"):
        raise ValueError("Email needs a From and Subject header")
    parts = list(parsed.walk())
    if len(parts) > 100:
        raise ValueError("Email contains too many MIME parts")
    texts = []
    remaining = MAX_EMAIL_TEXT
    for part in parts:
        if part.get_content_type() != "text/plain" or part.get_content_disposition() == "attachment" or part.is_multipart() or remaining <= 0:
            continue
        try:
            content = part.get_content()
        except (LookupError, ValueError, TypeError):
            content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        clean = _text(content, remaining)
        texts.append(clean)
        remaining -= len(clean)
    subject = _text(parsed.get("Subject"), 500)
    sender = _text(parseaddr(str(parsed.get("From")))[1], 320)
    text = "\n".join(texts)[:MAX_EMAIL_TEXT]
    searchable = (subject + "\n" + text).casefold()
    # These are hints from untrusted message text, never verified outcomes.
    company_match = bool(job.company.strip()) and job.company.strip().casefold() in searchable
    role_match = bool(job.title.strip()) and job.title.strip().casefold() in searchable
    if re.search(r"\b(regret|unfortunately|not selected|not moving forward|not move forward|position.{0,15}filled|unsuccessful)\b", searchable):
        classification, suggested = "rejection", "rejected"
    elif re.search(r"\b(interview|screening call|schedule a call)\b", searchable):
        classification, suggested = "interview", "interview"
    elif re.search(r"\b(application.{0,30}received|received.{0,30}application|thank you for applying|successfully submitted)\b", searchable):
        classification, suggested = "receipt", "applied"
    else:
        classification, suggested = "other", ""
    return {"subject": subject, "sender": sender, "date": _text(parsed.get("Date"), 100), "text": text, "classification": classification, "suggested_stage": suggested if company_match and role_match else "", "company_match": company_match, "role_match": role_match, "source": "authorized_eml_import", "trusted": False}


class TrackingStore:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    started_at TEXT NOT NULL, finished_at TEXT NOT NULL, state TEXT NOT NULL,
                    adapter TEXT NOT NULL, version TEXT NOT NULL, error_class TEXT NOT NULL, reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    attempt_id TEXT UNIQUE NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pipeline (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL, stage TEXT NOT NULL, note TEXT NOT NULL, evidence_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS email_evidence (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL, sha256 TEXT NOT NULL, data TEXT NOT NULL,
                    UNIQUE(job_id,sha256)
                );
                CREATE INDEX IF NOT EXISTS attempts_job ON attempts(job_id);
                CREATE INDEX IF NOT EXISTS receipts_job ON receipts(job_id);
                CREATE INDEX IF NOT EXISTS pipeline_job ON pipeline(job_id);
                CREATE INDEX IF NOT EXISTS email_evidence_job ON email_evidence(job_id);
            """)

    def _job(self, job_id):
        job = self.store.job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def start_attempt(self, job) -> str:
        self._job(job.id)
        attempt_id = uid()
        with self.store.connection() as db:
            db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?)", (attempt_id, job.id, now(), "", "applying", _text(job.adapter_name, 80), _text(job.adapter_version, 40), "", ""))
        return attempt_id

    def finish_attempt(self, attempt_id, job, result=None) -> None:
        state = _get(result, "state", job.status)
        if state not in ATTEMPT_STATES:
            state = "failed"
        reason = _text(_get(result, "reason", job.error), 1000)
        error_class = _text(job.last_error_class, 80)
        receipt = _receipt(_get(result, "receipt", None)) if state == "submitted" else None
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT job_id,finished_at FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None or row[0] != job.id:
                raise ValueError("Attempt does not belong to this job")
            if row[1]:
                return
            db.execute("UPDATE attempts SET finished_at=?,state=?,adapter=?,version=?,error_class=?,reason=? WHERE id=?", (now(), state, _text(job.adapter_name, 80), _text(job.adapter_version, 40), error_class, reason, attempt_id))
            if receipt:
                receipt["source"] = "observed_browser"
                db.execute("INSERT INTO receipts VALUES (?,?,?,?,?)", (uid(), job.id, attempt_id, now(), json.dumps(receipt)))

    def history(self, job_id) -> dict:
        self._job(job_id)
        with self.store.connection() as db:
            attempts = [dict(row) for row in db.execute("SELECT * FROM attempts WHERE job_id=? ORDER BY rowid", (job_id,))]
            receipts = [{"id": row["id"], "job_id": job_id, "attempt_id": row["attempt_id"], "created_at": row["created_at"], **json.loads(row["data"])} for row in db.execute("SELECT * FROM receipts WHERE job_id=? ORDER BY rowid", (job_id,))]
            stages = [dict(row) for row in db.execute("SELECT * FROM pipeline WHERE job_id=? ORDER BY rowid", (job_id,))]
            emails = [{"id": row["id"], "job_id": job_id, "created_at": row["created_at"], "sha256": row["sha256"], **json.loads(row["data"])} for row in db.execute("SELECT * FROM email_evidence WHERE job_id=? ORDER BY rowid", (job_id,))]
        return {"job_id": job_id, "stage": stages[-1]["stage"] if stages else "prospect", "attempts": attempts, "receipts": receipts, "stage_history": stages, "email_evidence": emails}

    def set_stage(self, job_id, stage, note="", evidence_id="") -> dict:
        self._job(job_id)
        if stage not in STAGES:
            raise ValueError("Choose a supported tracking stage")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if evidence_id:
                row = db.execute("SELECT data FROM email_evidence WHERE id=? AND job_id=?", (evidence_id, job_id)).fetchone()
                if row is None or json.loads(row[0]).get("suggested_stage") != stage:
                    raise ValueError("Email evidence does not suggest this stage for this exact job")
            db.execute("INSERT INTO pipeline VALUES (?,?,?,?,?,?)", (uid(), job_id, now(), stage, _text(note, 1000), evidence_id))
        return self.history(job_id)

    def import_email(self, job_id, raw: bytes, authorized=False) -> dict:
        if not authorized:
            raise ValueError("Explicit consent is required to import this email")
        job = self._job(job_id)
        data = _email(raw, job)
        digest = hashlib.sha256(raw).hexdigest()
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id,data,created_at FROM email_evidence WHERE job_id=? AND sha256=?", (job_id, digest)).fetchone()
            if existing:
                return {"id": existing[0], "job_id": job_id, "created_at": existing[2], **json.loads(existing[1]), "duplicate": True}
            evidence_id, created_at = uid(), now()
            db.execute("INSERT INTO email_evidence VALUES (?,?,?,?,?)", (evidence_id, job_id, created_at, digest, json.dumps(data)))
        return {"id": evidence_id, "job_id": job_id, "created_at": created_at, **data, "duplicate": False}

    def _export_jobs(self, job_id=None):
        if job_id:
            jobs = [self._job(job_id)]
        else:
            with self.store.connection() as db:
                jobs = [Job.model_validate_json(row[0]) for row in db.execute("SELECT data FROM jobs ORDER BY rowid")]
        return [{"job_id": job.id, "title": job.title, "company": job.company, "status": job.status, "history": self.history(job.id)} for job in jobs]

    def export_json(self, job_id=None) -> str:
        return json.dumps({"version": 1, "exported_at": now(), "jobs": self._export_jobs(job_id)}, ensure_ascii=False, indent=2)

    def export_csv(self, job_id=None) -> str:
        stream = io.StringIO(newline="")
        fields = ["job_id", "job_title", "company", "application_status", "pipeline_stage", "kind", "id", "attempt_id", "timestamp", "started_at", "finished_at", "created_at", "observed_at", "state", "stage", "adapter", "version", "error_class", "reason", "reference", "url", "subject", "sender", "date", "text", "note", "evidence_id", "source", "sha256", "classification", "suggested_stage", "company_match", "role_match", "trusted"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for job in self._export_jobs(job_id):
            history = job["history"]
            base = {"job_id": job["job_id"], "job_title": job["title"], "company": job["company"], "application_status": job["status"], "pipeline_stage": history["stage"]}
            rows = []
            for item in history["attempts"]:
                rows.append({"kind": "attempt", **item, "timestamp": item["started_at"]})
            for item in history["receipts"]:
                rows.append({"kind": "receipt", **item, "timestamp": item["created_at"]})
            for item in history["stage_history"]:
                rows.append({"kind": "stage", **item, "timestamp": item["created_at"], "state": item["stage"]})
            for item in history["email_evidence"]:
                rows.append({"kind": "email", **item, "timestamp": item["created_at"], "state": item["classification"]})
            if not rows:
                rows = [{"kind": "job"}]
            for row in rows:
                values = {**base, **row}
                writer.writerow({key: _csv_cell(values.get(key, "")) for key in fields})
        return stream.getvalue()
