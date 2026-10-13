"""Persistent, source-bound review and DOCX export for written drafts."""
from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Literal
from xml.etree.ElementTree import Element, tostring

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import Field

from .continuation import configuration_key
from .models import Config, Job, Model, Resume, now
from .resumes import MAX_UPLOAD
from .written_documents import document_is_current, draft_document


class DraftRequest(Model):
    kind: Literal['cover_letter', 'cv_tailoring']
    max_length: int = Field(default=4000, ge=1, le=20000)


class DraftReview(Model):
    confirmation: bool = False
    source_fingerprint: str = Field(min_length=1, max_length=128)


def _docx_bytes(draft: dict) -> bytes:
    document = Document()
    for line in draft['text'].split('\n'):
        document.add_paragraph(line)
    document.core_properties.title = draft['title'][:255]
    # Core comments have a 255-character limit. Preserve complete source IDs
    # there, and preserve the complete provenance in an attached XML part.
    comments = []
    for source in draft['sources']:
        if len('\n'.join([*comments, source])) > 255:
            break
        comments.append(source)
    document.core_properties.comments = '\n'.join(comments)
    evidence = Element('orbit-draft-evidence')
    evidence.text = json.dumps({key: draft[key] for key in ('id', 'kind', 'source_fingerprint', 'config_fingerprint', 'sources', 'facts', 'reviewed_at')}, ensure_ascii=False)
    part = Part(PackURI('/customXml/orbit-evidence.xml'), 'application/xml', tostring(evidence, encoding='utf-8', xml_declaration=True), document.part.package)
    document.part.relate_to(part, RELATIONSHIP_TYPE.CUSTOM_XML)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def register_written_document_routes(app, store, worker, runtime):
    """Register before static mounting; schema is additive and job-bound.

    Draft operations never queue applications or change the selected CV. Worker
    and runtime are accepted for the application registrar's common interface.
    """
    with store.connection() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS document_drafts (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS document_drafts_job ON document_drafts(job_id);
        ''')

    def dependencies(db, job_id):
        row = db.execute('SELECT data FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Job not found')
        job = Job.model_validate_json(row[0])
        config = Config.model_validate_json(db.execute('SELECT data FROM config WHERE id=1').fetchone()[0])
        row = db.execute('SELECT data FROM resumes WHERE id=?', (job.resume_id,)).fetchone()
        if not row:
            raise HTTPException(409, 'Select a confirmed CV for this job before drafting')
        resume = Resume.model_validate_json(row[0])
        if (not resume.confirmed or not job.resume_hash or resume.sha256 != job.resume_hash
                or (resume.conflicts and not resume.conflict_acknowledged)):
            raise HTTPException(409, 'The selected confirmed CV changed; prepare this application again')
        if resume.path:
            try:
                path = Path(resume.path)
                if not path.is_file() or path.stat().st_size > MAX_UPLOAD:
                    raise OSError('Source CV is missing or exceeds the upload limit')
                with path.open('rb') as source:
                    digest = hashlib.file_digest(source, 'sha256').hexdigest()
            except OSError as exc:
                raise HTTPException(409, 'The source CV file is unavailable; restore or select it again') from exc
            if digest != resume.sha256:
                raise HTTPException(409, 'The source CV file changed; import and confirm it again')
        return config, job, resume

    def stored_draft(db, draft_id):
        row = db.execute('SELECT data FROM document_drafts WHERE id=?', (draft_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Written draft not found')
        return json.loads(row[0])

    def current(draft, config, job, resume):
        return (draft.get('resume_id') == resume.id and draft.get('resume_hash') == resume.sha256
                and draft.get('config_fingerprint') == configuration_key(config)
                and document_is_current(draft, job, config.profile, resume))

    def assert_current(db, draft):
        config, job, resume = dependencies(db, draft['job_id'])
        if not current(draft, config, job, resume):
            raise HTTPException(409, 'Draft sources or configuration changed; generate and review a new draft')

    @app.get('/api/jobs/{id}/drafts')
    def list_drafts(id: str):
        with store.connection() as db:
            db.execute('BEGIN')
            if not db.execute('SELECT 1 FROM jobs WHERE id=?', (id,)).fetchone():
                raise HTTPException(404, 'Job not found')
            try:
                config, job, resume = dependencies(db, id)
                usable = True
            except HTTPException:
                usable = False
            result = []
            for row in db.execute('SELECT data FROM document_drafts WHERE job_id=? ORDER BY rowid DESC', (id,)):
                draft = json.loads(row[0])
                is_current = usable and current(draft, config, job, resume)
                result.append({**draft, 'current': is_current, 'reviewed': bool(draft['reviewed'] and is_current)})
            return result

    @app.post('/api/jobs/{id}/drafts')
    def generate_draft(id: str, body: DraftRequest):
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            config, job, resume = dependencies(db, id)
            try:
                draft = draft_document(body.kind, job, config.profile, resume, max_length=body.max_length)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            draft['config_fingerprint'] = configuration_key(config)
            db.execute('INSERT INTO document_drafts VALUES (?,?,?)', (draft['id'], job.id, json.dumps(draft)))
            return {**draft, 'current': True}

    @app.post('/api/drafts/{id}/review')
    def review_draft(id: str, body: DraftReview):
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            draft = stored_draft(db, id)
            if not body.confirmation:
                raise HTTPException(409, 'Explicitly confirm that you reviewed the draft and its sources')
            if body.source_fingerprint != draft['source_fingerprint']:
                raise HTTPException(409, 'The reviewed source fingerprint changed; reload the current draft')
            assert_current(db, draft)
            draft['reviewed'], draft['reviewed_at'] = True, now()
            db.execute('UPDATE document_drafts SET data=? WHERE id=?', (json.dumps(draft), id))
            return {**draft, 'current': True}

    @app.get('/api/drafts/{id}/download')
    def download_draft(id: str):
        with store.connection() as db:
            # Lock source DB dependencies until export bytes are assembled.
            db.execute('BEGIN IMMEDIATE')
            draft = stored_draft(db, id)
            if not draft['reviewed']:
                raise HTTPException(409, 'Review and explicitly confirm the written draft before downloading')
            assert_current(db, draft)
            payload = _docx_bytes(draft)
        filename = draft['kind'].replace('_', '-') + '-' + draft['id'] + '.docx'
        return Response(payload, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'no-store'})
