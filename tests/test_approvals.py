import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.approvals import ApprovalStore, build_snapshot
from agent.approval_api import register_approval_api
from agent.models import Job, Resume
from agent.store import Store


@pytest.fixture
def workspace(tmp_path):
    store = Store(tmp_path / 'agent.db')
    path = tmp_path / 'selected.pdf'
    path.write_bytes(b'original confirmed CV')
    cv = Resume(label='Selected CV', filename='selected.pdf', path=str(path), confirmed=True,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    store.save_resume(cv)
    job = store.add_job(Job(linkedin_id='review', title='Nurse', company='Clinic', status='needs_input',
        application_url='https://clinic.example/apply', resume_id=cv.id, resume_hash=cv.sha256,
        answers={'Preferred shift': 'Night'}, answer_sources={'Preferred shift': 'profile:preferences'},
        evidence=['Exact employer listing verified']))
    return store, ApprovalStore(store, tmp_path), job, cv


def test_snapshot_copies_reviewed_values_without_private_paths(workspace):
    store, approvals, job, cv = workspace
    snapshot = approvals.create(job.id)
    assert snapshot['payload']['cv'] == {'id':cv.id, 'sha256':cv.sha256, 'filename':'selected.pdf', 'label':'Selected CV'}
    assert snapshot['payload']['answers'] == {'Preferred shift':'Night'}
    assert snapshot['payload']['sources'] == {'Preferred shift':'profile:preferences'}
    assert snapshot['payload']['employer_evidence'] == ['Exact employer listing verified']
    assert snapshot['approved'] is False
    assert str(store.path.parent) not in json.dumps(snapshot)
    snapshot['payload']['answers']['Preferred shift'] = 'Day'
    assert approvals.latest(job.id)['payload']['answers']['Preferred shift'] == 'Night'


def test_approval_requires_explicit_confirmation_and_latest_token(workspace):
    store, approvals, job, cv = workspace
    snapshot = approvals.create(job.id)
    with pytest.raises(ValueError): approvals.approve(job.id, snapshot['token'], False)
    with pytest.raises(ValueError): approvals.approve(job.id, 'tampered', True)
    assert store.job(job.id).status == 'needs_input'
    store.hold_application(job.id)
    approved = approvals.approve(job.id, snapshot['token'], True)
    assert approved['approved'] is True
    assert store.job(job.id).status == 'queued'
    assert store.job(job.id).approved_revision == store.config().revision
    assert not store.application_held(job.id)
    assert approvals.payload_matches(store.job(job.id), store.config(), cv, job.answers, job.answer_sources)
    assert not approvals.payload_matches(store.job(job.id), store.config(), cv, {'Preferred shift':'Day'}, job.answer_sources)
    assert not approvals.payload_matches(store.job(job.id), store.config(), cv, job.answers, {'Preferred shift':'invented'})


@pytest.mark.parametrize('change', ['config', 'cv_bytes', 'cv_confirmation', 'cv_generation', 'answer', 'source', 'destination', 'evidence', 'job_version', 'skip', 'duplicate', 'applying', 'submission_uncertain'])
def test_stale_snapshot_never_queues_changed_application(workspace, change):
    store, approvals, job, cv = workspace
    snapshot = approvals.create(job.id)
    current = store.job(job.id)
    if change == 'config':
        config = store.config(); config.profile.first_name = 'Changed'; store.save_config(config)
    elif change == 'cv_bytes':
        from pathlib import Path
        Path(cv.path).write_bytes(b'replaced CV')
    elif change == 'cv_confirmation':
        cv.confirmed = False; store.save_resume(cv)
    elif change == 'cv_generation':
        cv.text = 'changed facts'; store.save_resume(cv)
    elif change == 'skip': store.job_action(job.id, 'skip')
    else:
        if change == 'answer': current.answers = {'Preferred shift':'Day'}
        elif change == 'source': current.answer_sources = {'Preferred shift':'other'}
        elif change == 'destination': current.application_url = 'https://other.example/apply'
        elif change == 'evidence': current.evidence = ['Changed evidence']
        elif change == 'job_version': current.error = 'A new preparation was performed'; current.updated_at = 'changed-version'
        elif change == 'duplicate': current.possible_duplicate = 'another-job'
        else: current.status = change
        store.save_job(current)
    with pytest.raises(ValueError): approvals.approve(job.id, snapshot['token'], True)
    assert approvals.latest(job.id)['approved'] is False
    assert store.job(job.id).status != 'queued'


def test_pause_revision_preserves_review_payload(workspace):
    store, approvals, job, cv = workspace
    snapshot = approvals.create(job.id)
    config = store.config(); config.settings.paused = False; store.save_config(config)
    assert build_snapshot(job, store.config(), cv)['token'] == snapshot['token']
    assert approvals.approve(job.id, snapshot['token'], True)['approved']


def test_new_snapshot_invalidates_older_approval_and_detects_database_tampering(workspace):
    store, approvals, job, cv = workspace
    first = approvals.create(job.id)
    approvals.approve(job.id, first['token'], True)
    second = approvals.create(job.id)
    assert second['id'] != first['id']
    assert not approvals.payload_matches(job, store.config(), cv, job.answers, job.answer_sources)
    with store.connection() as db:
        row = db.execute('SELECT data FROM approval_snapshots WHERE id=?',(second['id'],)).fetchone()
        data = json.loads(row[0]); data['payload']['answers']['Preferred shift']='Day'
        db.execute('UPDATE approval_snapshots SET data=? WHERE id=?',(json.dumps(data),second['id']))
    with pytest.raises(ValueError): approvals.approve(job.id, second['token'], True)


def test_document_metadata_participates_in_payload_digest(workspace):
    store, approvals, job, cv = workspace
    one = build_snapshot(job,store.config(),cv,documents=[{'id':'letter','sha256':'original','filename':'letter.pdf','kind':'cover_letter','path':'/private/secret'}])
    two = build_snapshot(job,store.config(),cv,documents=[{'id':'letter','sha256':'changed','filename':'letter.pdf','kind':'cover_letter'}])
    assert one['token'] != two['token']
    assert '/private/secret' not in json.dumps(one)


def test_api_blocks_running_reviews_and_queues_only_displayed_snapshot(workspace):
    store, approvals, job, cv = workspace
    app = FastAPI(); worker = SimpleNamespace(busy=False,run_lock=asyncio.Lock())
    register_approval_api(app,store,worker,store.path.parent)
    with TestClient(app) as client:
        url='/api/jobs/'+job.id+'/review'
        result=client.get(url); assert result.status_code == 200
        snapshot=result.json()
        assert client.get(url).json()['id'] == snapshot['id']
        assert client.post(url,json={'token':snapshot['token'],'confirmation':False}).status_code == 409
        worker.busy=True
        assert client.post(url,json={'token':snapshot['token'],'confirmation':True}).status_code == 409
        worker.busy=False
        assert client.post(url,json={'token':snapshot['token'],'confirmation':True}).status_code == 200
        assert store.job(job.id).status == 'queued'


def test_final_gate_rejects_changed_live_job_even_with_old_browser_object(workspace):
    store, approvals, job, cv = workspace
    snapshot=approvals.create(job.id); approvals.approve(job.id,snapshot['token'],True)
    current=store.job(job.id); current.evidence=['Employer ownership changed']; store.save_job(current)
    assert not approvals.payload_matches(job,store.config(),cv,job.answers,job.answer_sources)


def test_document_generation_change_requires_fresh_review(workspace):
    store, approvals, job, cv = workspace
    snapshot=approvals.create(job.id)
    store.meta('document_generation','1')
    with pytest.raises(ValueError): approvals.approve(job.id,snapshot['token'],True)
    assert store.job(job.id).status == 'needs_input'


def test_get_review_after_approval_retains_approved_snapshot(workspace):
    store, approvals, job, cv = workspace
    app=FastAPI(); worker=SimpleNamespace(busy=False,run_lock=asyncio.Lock())
    register_approval_api(app,store,worker,store.path.parent)
    with TestClient(app) as client:
        url='/api/jobs/'+job.id+'/review'
        first=client.get(url).json()
        assert client.post(url,json={'token':first['token'],'confirmation':True}).status_code == 200
        reviewed=client.get(url).json()
        assert reviewed['id'] == first['id']
        assert reviewed['approved'] is True


@pytest.mark.parametrize('change',['bytes','confirmation','kind','selection','deletion'])
def test_selected_original_document_changes_invalidate_approval(workspace,change):
    from pathlib import Path
    from agent.document_library import DocumentLibrary
    store,approvals,job,cv=workspace
    library=DocumentLibrary(store,store.path.parent)
    document=library.import_document(b'Original cover letter text','letter.txt','Cover letter','cover_letter')
    document.confirmed=True; document=library.save(document)
    job.document_ids=[document.id]; store.save_job(job)
    snapshot=approvals.create(job.id)
    assert snapshot['payload']['documents'] == [{'id':document.id,'sha256':document.sha256,'filename':'letter.txt','kind':'cover_letter'}]
    assert document.path not in json.dumps(snapshot)
    if change=='bytes': Path(document.path).write_bytes(b'Replaced original')
    elif change=='confirmation': document.confirmed=False; library.save(document)
    elif change=='kind': document.kind='portfolio'; library.save(document)
    elif change=='selection': job.document_ids=[]; store.save_job(job)
    else: library.delete(document.id)
    with pytest.raises(ValueError): approvals.approve(job.id,snapshot['token'],True)
    assert store.job(job.id).status != 'queued'


def test_final_gate_requires_actual_selected_document_metadata(workspace):
    from agent.document_library import DocumentLibrary
    store,approvals,job,cv=workspace
    library=DocumentLibrary(store,store.path.parent)
    document=library.import_document(b'Original cover letter text','letter.txt','Cover letter','cover_letter')
    document.confirmed=True; document=library.save(document)
    job.document_ids=[document.id]; store.save_job(job)
    snapshot=approvals.create(job.id); approvals.approve(job.id,snapshot['token'],True)
    assert approvals.payload_matches(job,store.config(),cv,job.answers,job.answer_sources,[document])
    assert not approvals.payload_matches(job,store.config(),cv,job.answers,job.answer_sources,[])


def test_approval_history_payload_remains_immutable(workspace):
    store,approvals,job,cv=workspace
    first=approvals.create(job.id)
    current=store.job(job.id); current.answers={'Preferred shift':'Day'}; store.save_job(current)
    second=approvals.create(job.id)
    with pytest.raises(ValueError): approvals.approve(job.id,first['token'],True)
    approvals.approve(job.id,second['token'],True)
    with store.connection() as db:
        old=json.loads(db.execute('SELECT data FROM approval_snapshots WHERE id=?',(first['id'],)).fetchone()[0])
    assert old['payload']['answers'] == {'Preferred shift':'Night'}
    assert old['approved'] is False


def test_snapshot_freezes_reviewable_configuration_without_pause_or_revision(workspace):
    store,approvals,job,cv=workspace
    config=store.config(); config.profile.first_name='Ada'; config.settings.daily_limit=3; config=store.save_config(config)
    snapshot=approvals.create(job.id)
    frozen=snapshot['payload']['configuration']
    assert frozen['profile']['first_name']=='Ada'
    assert frozen['settings']['daily_limit']==3
    assert 'paused' not in frozen['settings']
    assert 'revision' not in frozen
    config.profile.first_name='Changed'
    assert frozen['profile']['first_name']=='Ada'
    assert approvals.latest(job.id)['payload']['configuration']['profile']['first_name']=='Ada'
