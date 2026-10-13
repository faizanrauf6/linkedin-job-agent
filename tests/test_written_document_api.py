"""Persistent draft review and real DOCX exports, with no browser or network."""
import hashlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.models import Job, Resume
from agent.store import Store


@pytest.fixture
def workspace(tmp_path):
    from agent.written_document_api import register_written_document_routes
    store = Store(tmp_path / 'agent.db')
    config = store.config()
    config.profile.summary = 'I provide patient care.'
    config.profile.work_history = 'Supported inpatient ward care.'
    store.save_config(config)
    path = tmp_path / 'original.pdf'
    path.write_bytes(b'offline confirmed original CV')
    resume = Resume(label='Selected CV', confirmed=True, text='Supported inpatient ward care.',
                    path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    store.save_resume(resume)
    job = store.add_job(Job(linkedin_id='1', title='Registered Nurse', company='County Hospital',
                            description='Ward care', resume_id=resume.id, resume_hash=resume.sha256))
    app = FastAPI()
    register_written_document_routes(app, store, SimpleNamespace(busy=False), tmp_path)
    with TestClient(app) as client:
        yield client, store, job, resume, path


def generate(workspace, kind='cover_letter'):
    client, _, job, *_ = workspace
    response = client.post(f'/api/jobs/{job.id}/drafts', json={'kind': kind, 'max_length': 4000})
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, draft, **changes):
    body = {'confirmation': True, 'source_fingerprint': draft['source_fingerprint']} | changes
    return client.post(f"/api/drafts/{draft['id']}/review", json=body)


def test_generation_persists_attributed_draft_without_changing_job_or_cv(workspace):
    client, store, job, resume, _ = workspace
    original_job, original_cv = store.job(job.id).model_dump(), store.resumes()[0].model_dump()
    draft = generate(workspace)
    assert draft['reviewed'] is False and draft['current'] is True
    assert draft['sources'] and draft['facts']
    assert 'Supported inpatient ward care.' in draft['text']
    assert client.get(f'/api/jobs/{job.id}/drafts').json()[0]['id'] == draft['id']
    assert store.job(job.id).model_dump() == original_job
    assert store.resumes()[0].model_dump() == original_cv
    assert resume.id == draft['resume_id']


def test_unreviewed_export_and_implicit_or_wrong_token_review_are_blocked(workspace):
    client, *_ = workspace
    draft = generate(workspace)
    assert client.get(f"/api/drafts/{draft['id']}/download").status_code == 409
    assert approve(client, draft, confirmation=False).status_code == 409
    assert approve(client, draft, source_fingerprint='stale-token').status_code == 409
    assert client.get(f"/api/drafts/{draft['id']}/download").status_code == 409


@pytest.mark.parametrize('kind', ['cover_letter', 'cv_tailoring'])
def test_explicit_review_downloads_real_docx_with_exact_draft_text(workspace, kind):
    client, store, job, resume, path = workspace
    original_bytes = path.read_bytes()
    draft = generate(workspace, kind)
    reviewed = approve(client, draft)
    assert reviewed.status_code == 200 and reviewed.json()['reviewed'] is True
    response = client.get(f"/api/drafts/{draft['id']}/download")
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    assert '.docx' in response.headers['content-disposition']
    exported = Document(BytesIO(response.content))
    assert '\n'.join(paragraph.text for paragraph in exported.paragraphs) == draft['text']
    assert 'profile:work_history:0' in exported.core_properties.comments
    assert path.read_bytes() == original_bytes
    assert store.job(job.id).resume_id == resume.id
    assert len(store.resumes()) == 1


@pytest.mark.parametrize('changed', ['profile', 'cv_hash', 'cv_text', 'cv_confirmation', 'cv_bytes', 'cv_missing', 'selected_cv', 'job', 'settings'])
def test_stale_sources_block_review_and_previous_review_export(workspace, changed):
    client, store, job, resume, path = workspace
    draft = generate(workspace)
    assert approve(client, draft).status_code == 200
    if changed == 'profile':
        config = store.config(); config.profile.summary = 'I provide outpatient care.'; store.save_config(config)
    elif changed == 'settings':
        config = store.config(); config.settings.daily_limit += 1; store.save_config(config)
    elif changed == 'cv_bytes':
        path.write_bytes(b'changed bytes')
    elif changed == 'cv_missing':
        path.unlink()
    elif changed == 'selected_cv':
        alternate = Resume(label='Other CV', confirmed=True, sha256='other-hash'); store.save_resume(alternate)
        job.resume_id, job.resume_hash = alternate.id, alternate.sha256; store.save_job(job)
    elif changed == 'job':
        job.description = 'Changed role'; store.save_job(job)
    else:
        if changed == 'cv_hash': resume.sha256 = 'changed-hash'
        elif changed == 'cv_text': resume.text = 'Managed 5 clinic rooms.'
        else: resume.confirmed = False
        store.save_resume(resume)
    assert approve(client, draft).status_code == 409
    assert client.get(f"/api/drafts/{draft['id']}/download").status_code == 409
    listed = client.get(f'/api/jobs/{job.id}/drafts').json()[0]
    assert listed['current'] is False and listed['reviewed'] is False
    assert listed['text'] == draft['text']


@pytest.mark.parametrize('changed', ['unconfirmed', 'hash', 'bytes', 'missing_selection'])
def test_generation_requires_exact_confirmed_source_cv(workspace, changed):
    client, store, job, resume, path = workspace
    if changed == 'unconfirmed':
        resume.confirmed = False; store.save_resume(resume)
    elif changed == 'hash':
        job.resume_hash = 'wrong'; store.save_job(job)
    elif changed == 'bytes':
        path.write_bytes(b'changed')
    else:
        job.resume_id = ''; store.save_job(job)
    response = client.post(f'/api/jobs/{job.id}/drafts', json={'kind': 'cover_letter'})
    assert response.status_code == 409


def test_invalid_generation_input_and_empty_facts_are_422(workspace):
    client, store, job, resume, _ = workspace
    for body in ({'kind': 'unknown'}, {'kind': 'cover_letter', 'max_length': 0}, {'kind': 'cover_letter', 'max_length': 1}):
        assert client.post(f'/api/jobs/{job.id}/drafts', json=body).status_code == 422
    config = store.config(); config.profile.summary = ''; config.profile.work_history = ''; store.save_config(config)
    resume.text = 'Unlabeled Heading'; store.save_resume(resume)
    assert client.post(f'/api/jobs/{job.id}/drafts', json={'kind': 'cover_letter'}).status_code == 422


def test_unknown_job_and_draft_are_404_and_job_delete_cascades(workspace):
    client, store, job, *_ = workspace
    assert client.get('/api/jobs/missing/drafts').status_code == 404
    assert client.post('/api/jobs/missing/drafts', json={'kind': 'cover_letter'}).status_code == 404
    assert client.get('/api/drafts/missing/download').status_code == 404
    assert client.post('/api/drafts/missing/review', json={'confirmation': True, 'source_fingerprint': 'missing'}).status_code == 404
    draft = generate(workspace)
    with store.connection() as db:
        db.execute('DELETE FROM jobs WHERE id=?', (job.id,))
        assert db.execute('SELECT COUNT(*) FROM document_drafts').fetchone()[0] == 0
    assert client.get(f"/api/drafts/{draft['id']}/download").status_code == 404


def test_docx_embeds_complete_unmodified_source_evidence(workspace):
    import json
    import zipfile
    from xml.etree.ElementTree import fromstring
    client, *_ = workspace
    draft = generate(workspace)
    assert approve(client, draft).status_code == 200
    response = client.get(f"/api/drafts/{draft['id']}/download")
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        evidence = json.loads(fromstring(archive.read('customXml/orbit-evidence.xml')).text)
    assert evidence['sources'] == draft['sources']
    assert evidence['facts'] == draft['facts']
    assert evidence['source_fingerprint'] == draft['source_fingerprint']
    assert evidence['config_fingerprint'] == draft['config_fingerprint']
    assert evidence['reviewed_at']


def test_review_endpoint_cannot_replace_text_sources_or_attachment(workspace):
    client, *_ = workspace
    draft = generate(workspace)
    for field, value in [('text', 'I hold an unknown license.'), ('sources', ['fabricated']), ('resume_id', 'other')]:
        assert approve(client, draft, **{field: value}).status_code == 422
    assert client.get(f"/api/drafts/{draft['id']}/download").status_code == 409
