from types import SimpleNamespace
import pytest
from agent.models import ApplyResult,Job
from agent.tracking import TrackingStore
from agent.worker import Worker
from test_continuation_worker import configured_store,RetainedBrowser


@pytest.mark.asyncio
async def test_worker_records_attempt_and_observed_receipt_in_durable_history(tmp_path):
    store,config,cv=configured_store(tmp_path)
    job=store.add_job(Job(linkedin_id='1',title='Python engineer',company='Acme',status='queued',
        resume_id=cv.id,resume_hash=cv.sha256,approved_revision=config.revision))
    browser=RetainedBrowser();browser.pending_application=SimpleNamespace(job_id=job.id)
    async def apply(job,profile,resume,config,before_submit,can_act):
        assert before_submit()
        return ApplyResult(state='submitted',receipt={'text':'Application received','reference':'fixture-123','url':'https://careers.example/confirmation'})
    browser.apply=apply
    await Worker(store,browser).process_one()
    history=TrackingStore(store).history(job.id)
    assert history['attempts'][0]['state']=='submitted'
    assert history['receipts'][0]['reference']=='fixture-123'
