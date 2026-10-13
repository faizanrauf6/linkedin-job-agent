from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from .models import Config, Job, Resume, now, uid


def canonical_url(url: str) -> str:
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_") and k.lower() not in {"source", "ref", "refid", "trackingid", "trk", "lever-source"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(query)), ""))


def within_hours(settings, instant: datetime | None = None) -> bool:
    time = (instant or datetime.now(ZoneInfo(settings.timezone))).astimezone(ZoneInfo(settings.timezone)).strftime("%H:%M")
    start, end = settings.operating_start, settings.operating_end
    if start == end:
        return True
    return start <= time < end if start < end else time >= start or time < end


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS job_keys (key TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS resumes (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL, job_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS search_evaluations (
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    search_id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(job_id, search_id)
                );
                CREATE TABLE IF NOT EXISTS manual_skips (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS evaluation_versions (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                    resume_generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS application_holds (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
            """)
            db.execute("INSERT OR IGNORE INTO config VALUES (1, ?)", (Config().model_dump_json(),))
            # Preserve skips from databases created before the separate marker.
            for row in db.execute("SELECT data FROM jobs WHERE status='rejected'").fetchall():
                job = Job.model_validate_json(row[0])
                if job.error == "Skipped by user":
                    db.execute("INSERT OR IGNORE INTO manual_skips VALUES (?)", (job.id,))
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def config(self) -> Config:
        with self.connection() as db:
            return Config.model_validate_json(db.execute("SELECT data FROM config WHERE id=1").fetchone()[0])

    def save_config(self, config: Config) -> Config:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = Config.model_validate_json(db.execute("SELECT data FROM config WHERE id=1").fetchone()[0])
            if config.revision != current.revision:
                raise ValueError("Settings changed in another window. Reload before saving.")
            before, after = current.model_dump(), config.model_dump()
            for snapshot in (before, after):
                snapshot.pop("revision")
                snapshot["settings"].pop("paused")
            preserve_approval = before == after
            config = config.model_copy(deep=True)
            config.revision = current.revision + 1
            db.execute("UPDATE config SET data=? WHERE id=1", (config.model_dump_json(),))
            if preserve_approval:
                for row in db.execute("SELECT data FROM jobs WHERE status='queued'").fetchall():
                    job = Job.model_validate_json(row[0])
                    if job.approved_revision == current.revision:
                        job.approved_revision = config.revision
                        db.execute("UPDATE jobs SET data=? WHERE id=?", (job.model_dump_json(), job.id))
        return config

    def add_job(self, job: Job) -> Job:
        keys = ["linkedin:" + job.linkedin_id]
        if job.application_url:
            keys.append("url:" + canonical_url(job.application_url))
        if job.requisition:
            keys.append("req:" + job.company.casefold() + ":" + job.requisition)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT job_id FROM job_keys WHERE key IN (" + ",".join("?" for _ in keys) + ")", keys).fetchone()
            if existing:
                for key in keys:
                    db.execute("INSERT OR IGNORE INTO job_keys VALUES (?,?)", (key, existing[0]))
                return Job.model_validate_json(db.execute("SELECT data FROM jobs WHERE id=?", (existing[0],)).fetchone()[0])
            for row in db.execute("SELECT data FROM jobs"):
                prior = Job.model_validate_json(row[0])
                if prior.company.casefold().strip() == job.company.casefold().strip() and prior.title.casefold().strip() == job.title.casefold().strip() and prior.country.casefold() == job.country.casefold():
                    if prior.requisition and job.requisition and prior.requisition != job.requisition:
                        continue
                    job.status, job.possible_duplicate = "needs_input", prior.id
                    job.error = "Possible duplicate vacancy across listings. Compare the employer requisition before applying."
                    break
            db.execute("INSERT INTO jobs VALUES (?,?,?)", (job.id, job.status, job.model_dump_json()))
            db.executemany("INSERT INTO job_keys VALUES (?,?)", [(key, job.id) for key in keys])
        return job

    def jobs(self, limit: int = 500) -> list[Job]:
        with self.connection() as db:
            return [Job.model_validate_json(row[0]) for row in db.execute("SELECT data FROM jobs ORDER BY rowid DESC LIMIT ?", (limit,))]

    def linkedin_ids(self) -> set[str]:
        """Include aliases of canonical vacancies, not just their original listing."""
        with self.connection() as db:
            return {row[0][len("linkedin:"):] for row in db.execute("SELECT key FROM job_keys WHERE key LIKE 'linkedin:%'")}

    def job_evaluations(self, id: str) -> list[dict]:
        """Last decision for each search, including its configuration revision."""
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM search_evaluations WHERE job_id=? ORDER BY search_id", (id,))]

    def save_job_evaluations(self, id: str, evaluations: list[dict]):
        with self.connection() as db:
            db.executemany("""INSERT INTO search_evaluations VALUES (?,?,?,?)
                ON CONFLICT(job_id,search_id) DO UPDATE SET revision=excluded.revision,data=excluded.data
                WHERE excluded.revision >= search_evaluations.revision""",
                [(id, item["search_id"], item["revision"], json.dumps(item)) for item in evaluations])

    def manually_skipped(self, id: str) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM manual_skips WHERE job_id=?", (id,)).fetchone() is not None

    def hold_application(self, id: str):
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO application_holds VALUES (?)', (id,))

    def application_held(self, id: str) -> bool:
        with self.connection() as db:
            return db.execute('SELECT 1 FROM application_holds WHERE job_id=?', (id,)).fetchone() is not None

    def checkpoint(self, id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute('SELECT data FROM checkpoints WHERE job_id=?', (id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_checkpoint(self, id: str, data: dict):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM jobs WHERE id=?', (id,)).fetchone()
            if not row:
                return
            job = Job.model_validate_json(row[0])
            if job.submission_started or job.status in {'submitted', 'submission_uncertain'} or db.execute('SELECT 1 FROM manual_skips WHERE job_id=?', (id,)).fetchone():
                return
            db.execute('INSERT INTO checkpoints VALUES (?,?) ON CONFLICT(job_id) DO UPDATE SET data=excluded.data', (id, json.dumps(data)))

    def clear_checkpoint(self, id: str):
        with self.connection() as db:
            db.execute('DELETE FROM checkpoints WHERE job_id=?', (id,))

    @staticmethod
    def _resume_generation(db) -> int:
        row = db.execute("SELECT value FROM meta WHERE key='resume_generation'").fetchone()
        return int(row[0]) if row else 0

    def resume_generation(self) -> int:
        with self.connection() as db:
            return self._resume_generation(db)

    def job_resume_generation(self, id: str) -> int | None:
        with self.connection() as db:
            row = db.execute("SELECT resume_generation FROM evaluation_versions WHERE job_id=?", (id,)).fetchone()
            return row[0] if row else None

    def save_reevaluated_job(self, job: Job, original: Job, revision: int, resume_generation: int, *, hold: bool = False) -> bool:
        """Commit only if no user action, claim or config edit raced verification."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            config = Config.model_validate_json(db.execute("SELECT data FROM config WHERE id=1").fetchone()[0])
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job.id,)).fetchone()
            skipped = db.execute("SELECT 1 FROM manual_skips WHERE job_id=?", (job.id,)).fetchone()
            if not row or skipped or config.revision != revision or self._resume_generation(db) != resume_generation:
                return False
            fresh = Job.model_validate_json(row[0])
            if fresh.model_dump() != original.model_dump():
                return False
            job.updated_at = now()
            db.execute("UPDATE jobs SET status=?,data=? WHERE id=?", (job.status, job.model_dump_json(), job.id))
            db.execute("INSERT INTO evaluation_versions VALUES (?,?) ON CONFLICT(job_id) DO UPDATE SET resume_generation=excluded.resume_generation", (job.id, resume_generation))
            if hold:
                db.execute('INSERT OR IGNORE INTO application_holds VALUES (?)', (job.id,))
            return True

    def job(self, id: str) -> Job | None:
        with self.connection() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (id,)).fetchone()
            return Job.model_validate_json(row[0]) if row else None

    def save_job(self, job: Job):
        job.updated_at = now()
        with self.connection() as db:
            db.execute("UPDATE jobs SET status=?,data=? WHERE id=?", (job.status, job.model_dump_json(), job.id))

    def claim(self, preferred_id: str | None = None) -> Job | None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            instant = datetime.now(timezone.utc)
            suspended = {p["host"] for p in self.portals() if datetime.fromisoformat(p["suspended_until"]) > instant}
            job = None
            for row in db.execute("SELECT data FROM jobs WHERE status='queued' AND id NOT IN (SELECT job_id FROM manual_skips) AND id NOT IN (SELECT job_id FROM application_holds) ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END,rowid", (preferred_id,)):
                candidate = Job.model_validate_json(row[0])
                if candidate.submission_started or candidate.retry_at and datetime.fromisoformat(candidate.retry_at) > instant:
                    continue
                if self.portal_host(candidate) in suspended:
                    continue
                job = candidate
                break
            if job is None:
                return None
            job.status = "applying"
            job.attempts += 1
            job.last_error_class, job.retry_at = "", ""
            job.updated_at = now()
            db.execute("UPDATE jobs SET status=?,data=? WHERE id=?", (job.status, job.model_dump_json(), job.id))
            return job

    @staticmethod
    def portal_host(job: Job) -> str:
        return (urlsplit(job.url if job.easy_apply else job.application_url).hostname or "www.linkedin.com").lower()

    def portals(self) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT value FROM meta WHERE key LIKE 'portal:%' ORDER BY key")]

    def suspend_portal(self, host: str, reason: str, minutes: int):
        self.meta("portal:" + host.lower(), json.dumps({"host": host.lower(), "reason": reason[:300], "suspended_until": (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()}))

    def resume_portal(self, host: str) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM meta WHERE key=?", ("portal:" + host.lower(),))
            for row in db.execute("SELECT data FROM jobs WHERE status='needs_input'").fetchall():
                job = Job.model_validate_json(row[0])
                skipped = db.execute('SELECT 1 FROM manual_skips WHERE job_id=?', (job.id,)).fetchone()
                if not skipped and self.portal_host(job) == host.lower() and job.last_error_class == "PortalIntervention" and not job.submission_started and not job.possible_duplicate:
                    job.status, job.error, job.retry_at, job.attempts = "queued", "Portal resumed by user", "", 0
                    job.last_error_class = ""
                    db.execute('DELETE FROM application_holds WHERE job_id=?', (job.id,))
                    job.updated_at = now()
                    db.execute("UPDATE jobs SET status=?,data=? WHERE id=?", (job.status, job.model_dump_json(), job.id))

    def portal_suspended(self, host: str) -> bool:
        return any(p["host"] == host.lower() and datetime.fromisoformat(p["suspended_until"]) > datetime.now(timezone.utc) for p in self.portals())

    def schedule_retry(self, job: Job, settings, error_class: str) -> bool:
        job.last_error_class = error_class
        job.retry_at = ""
        if job.submission_started or job.attempts >= settings.retry_attempts:
            return False
        delay = settings.retry_backoff_seconds * 2 ** max(0, job.attempts - 1)
        job.status = "queued"
        job.retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        return True

    def recover(self):
        settings = self.config().settings
        for job in self.jobs(100000):
            if job.status == "applying":
                job.status = "submission_uncertain" if job.submission_started else "queued"
                job.error = "Interrupted submission: reconcile before retry" if job.submission_started else "Interrupted before submission; queued again"
                job.retry_at = ""
                if not job.submission_started and not self.schedule_retry(job, settings, "InterruptedOperation"):
                    job.status = "needs_input"
                    job.error = "Interrupted before submission; attempt limit reached. Inspect before queueing again."
                self.save_job(job)

    def _daily_count(self, db, config: Config) -> int:
        zone = ZoneInfo(config.settings.timezone)
        today = datetime.now(zone).date()
        count = 0
        for row in db.execute("SELECT data FROM jobs"):
            job = Job.model_validate_json(row[0])
            if job.submission_at and datetime.fromisoformat(job.submission_at).astimezone(zone).date() == today:
                count += 1
        return count

    def daily_count(self) -> int:
        with self.connection() as db:
            return self._daily_count(db, self.config())

    def begin_submission(self, id: str, revision: int) -> bool:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            config = Config.model_validate_json(db.execute("SELECT data FROM config WHERE id=1").fetchone()[0])
            row = db.execute("SELECT data FROM jobs WHERE id=?", (id,)).fetchone()
            if not row:
                return False
            job = Job.model_validate_json(row[0])
            if config.settings.paused or config.revision != revision or not within_hours(config.settings) or self._daily_count(db, config) >= config.settings.daily_limit or job.status != "applying" or job.submission_started:
                return False
            if config.settings.mode == "review" and job.approved_revision != config.revision or job.possible_duplicate:
                return False
            row = db.execute("SELECT data FROM resumes WHERE id=?", (job.resume_id,)).fetchone()
            if not row:
                return False
            resume = Resume.model_validate_json(row[0])
            from .resumes import assess_resume
            peers = [Resume.model_validate_json(r[0]) for r in db.execute("SELECT data FROM resumes WHERE id != ?", (resume.id,))]
            assess_resume(resume, config.profile, peers)
            if resume.conflicts and not resume.conflict_acknowledged:
                resume.confirmed = False
                db.execute("UPDATE resumes SET data=? WHERE id=?", (resume.model_dump_json(), resume.id))
                return False
            if not resume.confirmed or not job.resume_hash or resume.sha256 != job.resume_hash:
                return False
            if not resume.path or not Path(resume.path).is_file() or hashlib.sha256(Path(resume.path).read_bytes()).hexdigest() != job.resume_hash:
                return False
            job.submission_started = True
            db.execute('DELETE FROM checkpoints WHERE job_id=?', (id,))
            job.submission_at = now()
            db.execute("UPDATE jobs SET data=? WHERE id=?", (job.model_dump_json(), id))
            return True

    def can_act(self, id: str, revision: int) -> bool:
        config, job = self.config(), self.job(id)
        return bool(job and job.status == "applying" and not config.settings.paused and config.revision == revision and within_hours(config.settings) and not job.submission_started)

    def job_action(self, id: str, action: str, confirmation: bool = False, *, expected: Job | None = None,
                   revision: int | None = None, resume_generation: int | None = None, checkpoint_update: tuple[dict,dict] | None = None) -> Job:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM jobs WHERE id=?", (id,)).fetchone()
            if not row:
                raise KeyError(id)
            job = Job.model_validate_json(row[0])
            config = Config.model_validate_json(db.execute("SELECT data FROM config WHERE id=1").fetchone()[0])
            if (expected is not None and job.model_dump() != expected.model_dump()
                    or revision is not None and config.revision != revision
                    or resume_generation is not None and self._resume_generation(db) != resume_generation):
                raise ValueError('The application, settings or CV changed. Review the current state before continuing.')
            if checkpoint_update is not None:
                row = db.execute('SELECT data FROM checkpoints WHERE job_id=?', (id,)).fetchone()
                if row is None or json.loads(row[0]) != checkpoint_update[0]:
                    raise ValueError('The application checkpoint changed. Review it again.')
            if job.status == "applying":
                raise ValueError("This application is currently in progress")
            if action == "queue":
                if job.status in {"submitted", "submission_uncertain", "blocked"} or job.submission_started or job.possible_duplicate:
                    raise ValueError("Reconcile this application's outcome or duplicate identity first")
                job.status, job.error, job.approved_revision = "queued", "", config.revision
                job.retry_at = ""
                job.attempts = 0
                db.execute("DELETE FROM manual_skips WHERE job_id=?", (id,))
                db.execute('DELETE FROM application_holds WHERE job_id=?', (id,))
            elif action in {"mark_submitted", "retry_unsubmitted"}:
                if job.status != "submission_uncertain" or not confirmation:
                    raise ValueError("Confirm the application outcome on the destination first")
                job.status = "submitted" if action == "mark_submitted" else "queued"
                if action == "retry_unsubmitted":
                    job.submission_started, job.submission_at = False, ""
                    job.approved_revision = config.revision
                    job.retry_at, job.attempts = "", 0
                    db.execute('DELETE FROM application_holds WHERE job_id=?', (id,))
                job.error = "Outcome reconciled by user"
            elif action == "distinct_vacancy":
                if not job.possible_duplicate or not confirmation:
                    raise ValueError("Confirm these are distinct employer requisitions")
                job.possible_duplicate = ""
                job.status, job.error = "awaiting_review", "Distinct vacancy confirmed by user"
            elif action == "skip":
                if job.submission_started:
                    raise ValueError("Reconcile the submission first")
                job.status, job.error = "rejected", "Skipped by user"
                db.execute("INSERT OR IGNORE INTO manual_skips VALUES (?)", (id,))
                db.execute('DELETE FROM checkpoints WHERE job_id=?', (id,))
            else:
                raise ValueError("Unsupported action")
            job.updated_at = now()
            db.execute("UPDATE jobs SET status=?,data=? WHERE id=?", (job.status, job.model_dump_json(), id))
            if checkpoint_update is not None:
                db.execute('UPDATE checkpoints SET data=? WHERE job_id=?', (json.dumps(checkpoint_update[1]), id))
            return job

    def resumes(self) -> list[Resume]:
        with self.connection() as db:
            return [Resume.model_validate_json(row[0]) for row in db.execute("SELECT data FROM resumes ORDER BY rowid DESC")]

    def save_resume(self, resume: Resume):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            data = resume.model_dump_json()
            existing = db.execute("SELECT data FROM resumes WHERE id=?", (resume.id,)).fetchone()
            if existing and Resume.model_validate_json(existing[0]).model_dump() == resume.model_dump():
                return
            db.execute("INSERT INTO resumes VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (resume.id, data))
            db.execute("INSERT INTO meta VALUES ('resume_generation',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(self._resume_generation(db) + 1),))

    def delete_resume(self, id: str):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if any(j.resume_id == id and j.status in {"queued", "applying"} for j in self.jobs()):
                raise ValueError("This CV is attached to pending applications")
            if db.execute("DELETE FROM resumes WHERE id=?", (id,)).rowcount:
                db.execute("INSERT INTO meta VALUES ('resume_generation',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(self._resume_generation(db) + 1),))

    def event(self, message: str, level: str = "info", job_id: str = ""):
        with self.connection() as db:
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (uid(), now(), level, message, job_id))

    def events(self, limit: int = 100):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events ORDER BY rowid DESC LIMIT ?", (limit,))]

    def meta(self, key: str, value: str | None = None) -> str:
        with self.connection() as db:
            if value is not None:
                db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
                return value
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else ""

    def clear(self):
        with self.connection() as db:
            db.executescript("DELETE FROM checkpoints; DELETE FROM application_holds; DELETE FROM evaluation_versions; DELETE FROM search_evaluations; DELETE FROM manual_skips; DELETE FROM job_keys; DELETE FROM jobs; DELETE FROM resumes; DELETE FROM events; DELETE FROM meta;")
            db.execute("UPDATE config SET data=? WHERE id=1", (Config().model_dump_json(),))
