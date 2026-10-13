"""Immutable, locally reviewed application payloads and atomic queue approval."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .continuation import configuration_key
from .models import Config, Job, Resume, now


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _document_metadata(document):
    data = document if isinstance(document, dict) else document.model_dump(mode='json')
    return {key: data.get(key, '') for key in ('id', 'sha256', 'filename', 'kind')}


def build_snapshot(job, config, resume, documents=None):
    """Copy public review facts; timestamps and identifiers are outside the digest."""
    configuration=config.model_dump(mode='json',exclude={'revision'})
    configuration['settings'].pop('paused',None)
    payload = {
        'job': {key:getattr(job,key,'') for key in ('id','title','company','description','requisition','easy_apply','url','application_url','company_website','company_linkedin_url')},
        'cv': {key:getattr(resume,key,'') for key in ('id','sha256','filename','label')},
        'answers':dict(job.answers), 'sources':dict(job.answer_sources),
        'employer_evidence':list(job.evidence), 'config_key':configuration_key(config),
        'configuration':configuration,
        'documents':[_document_metadata(doc) for doc in documents or []],
    }
    payload['job']['target'] = job.url if job.easy_apply else job.application_url
    payload = json.loads(json.dumps(payload, ensure_ascii=False))
    return {'id':uuid4().hex,'job_id':job.id,'job_version':job.updated_at,'payload':payload,
            'token':_digest(payload),'approved':False,'created_at':now()}


class ApprovalStore:
    def __init__(self, store, runtime):
        self.store, self.runtime = store, Path(runtime)
        with store.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS approval_snapshots (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                data TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS approval_snapshots_job ON approval_snapshots(job_id)')

    @staticmethod
    def _latest(db, job_id):
        row=db.execute('SELECT data FROM approval_snapshots WHERE job_id=? ORDER BY rowid DESC LIMIT 1',(job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def latest(self, job_id):
        with self.store.connection() as db:
            return self._latest(db,job_id)

    @staticmethod
    def _verify_resume(resume, job):
        if not resume or not resume.confirmed or not resume.sha256 or resume.sha256 != job.resume_hash:
            raise ValueError('The selected confirmed CV changed. Prepare this application again.')
        if resume.conflicts and not resume.conflict_acknowledged:
            raise ValueError('Resolve the selected CV identity conflict before approving.')
        try:
            valid=resume.path and Path(resume.path).is_file() and hashlib.sha256(Path(resume.path).read_bytes()).hexdigest() == resume.sha256
        except OSError:
            valid=False
        if not valid:
            raise ValueError('The selected CV file is missing or changed. Prepare this application again.')

    def _documents(self, job, db):
        identifiers=list(getattr(job,'document_ids',[]) or [])
        if not identifiers:
            return []
        from .document_library import DocumentRecord, verified_document_path
        available={row['id']:DocumentRecord.model_validate_json(row['data']) for row in db.execute('SELECT id,data FROM documents')}
        selected=[]
        for identifier in identifiers:
            document=available.get(identifier)
            if not document or not document.confirmed:
                raise ValueError('A selected application document is missing or unconfirmed.')
            verified_document_path(document,self.runtime)
            selected.append(document)
        return selected

    @staticmethod
    def _document_generation(db):
        row=db.execute("SELECT value FROM meta WHERE key='document_generation'").fetchone()
        return int(row[0]) if row else 0

    def _context(self, db, job_id):
        row=db.execute('SELECT data FROM jobs WHERE id=?',(job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        job=Job.model_validate_json(row[0])
        config=Config.model_validate_json(db.execute('SELECT data FROM config WHERE id=1').fetchone()[0])
        row=db.execute('SELECT data FROM resumes WHERE id=?',(job.resume_id,)).fetchone()
        resume=Resume.model_validate_json(row[0]) if row else None
        self._verify_resume(resume,job)
        documents=self._documents(job,db)
        return job,config,resume,documents

    @staticmethod
    def _reviewable(db, job):
        if (job.submission_started or job.possible_duplicate or job.status in {'applying','submitted','submission_uncertain','blocked','rejected'}
                or db.execute('SELECT 1 FROM manual_skips WHERE job_id=?',(job.id,)).fetchone()):
            raise ValueError('This application cannot be approved in its current state.')

    def create(self, job_id):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            job,config,resume,documents=self._context(db,job_id)
            self._reviewable(db,job)
            snapshot=build_snapshot(job,config,resume,documents)
            snapshot['resume_generation']=self.store._resume_generation(db)
            snapshot['document_generation']=self._document_generation(db)
            db.execute('INSERT INTO approval_snapshots VALUES (?,?,?)',(snapshot['id'],job.id,json.dumps(snapshot)))
            return snapshot

    def approve(self, job_id, token, confirmation, *, paused_only=False):
        if not confirmation:
            raise ValueError('Explicitly approve the displayed application before queuing it.')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            snapshot=self._latest(db,job_id)
            job,config,resume,documents=self._context(db,job_id)
            self._reviewable(db,job)
            if paused_only and not config.settings.paused:
                raise ValueError('Pause the agent before approving an application.')
            current=build_snapshot(job,config,resume,documents)
            if (not snapshot or snapshot['token'] != token or _digest(snapshot['payload']) != token
                    or snapshot['job_version'] != job.updated_at
                    or snapshot.get('resume_generation') != self.store._resume_generation(db)
                    or snapshot.get('document_generation') != self._document_generation(db)
                    or current['payload'] != snapshot['payload']):
                raise ValueError('The application review is stale. Review the current application again.')
            job.status,job.error,job.approved_revision='queued','',config.revision
            job.retry_at,job.attempts='',0
            job.updated_at=now()
            db.execute('UPDATE jobs SET status=?,data=? WHERE id=?',(job.status,job.model_dump_json(),job.id))
            db.execute('DELETE FROM application_holds WHERE job_id=?',(job.id,))
            snapshot['approved']=True
            snapshot['approved_at']=now()
            snapshot['approved_job_version']=job.updated_at
            db.execute('UPDATE approval_snapshots SET data=? WHERE id=?',(json.dumps(snapshot),snapshot['id']))
            return snapshot

    def payload_matches(self, job, config, resume, answers, sources, documents=None):
        """Final gate: compare actual filled values to the latest approved payload."""
        try:
            self._verify_resume(resume,job)
            with self.store.connection() as db:
                snapshot=self._latest(db,job.id)
                if not snapshot or not snapshot.get('approved') or _digest(snapshot['payload']) != snapshot['token']:
                    return False
                if (snapshot.get('resume_generation') != self.store._resume_generation(db)
                        or snapshot.get('document_generation') != self._document_generation(db)):
                    return False
                current_job,current_config,current_resume,current_documents=self._context(db,job.id)
                if (current_job.submission_started or current_job.status in {'submitted','submission_uncertain'}
                        or current_job.possible_duplicate or db.execute('SELECT 1 FROM manual_skips WHERE job_id=?',(job.id,)).fetchone()):
                    return False
                if build_snapshot(current_job,current_config,current_resume,current_documents)['payload'] != snapshot['payload']:
                    return False
                if configuration_key(current_config) != configuration_key(config):
                    return False
                if current_resume.id != resume.id or current_resume.sha256 != resume.sha256:
                    return False
                if [_document_metadata(doc) for doc in current_documents] != [_document_metadata(doc) for doc in documents or []]:
                    return False
            candidate=job.model_copy(deep=True)
            candidate.answers,candidate.answer_sources=dict(answers),dict(sources)
            return build_snapshot(candidate,config,resume,documents)['payload'] == snapshot['payload']
        except (ValueError,KeyError,OSError,ImportError):
            return False
