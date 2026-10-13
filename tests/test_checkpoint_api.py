from fastapi.testclient import TestClient
from agent.api import create_app
from agent.models import Job, Resume
from agent.continuation import checkpoint_binding


def setup_workspace(tmp_path):
    app = create_app(tmp_path, run_worker=False)
    store = app.state.store
    cv = Resume(label='CV', confirmed=True, sha256='hash')
    store.save_resume(cv)
    job = store.add_job(Job(linkedin_id='1',title='Nurse',company='Clinic',status='needs_input',resume_id=cv.id,resume_hash=cv.sha256))
    data = {'version':1,'binding':checkpoint_binding(job,store.config(),cv),'step_signature':'step','confirmed':{},
        'field_details':[{'key':'shift','label':'Preferred shift','type':'select-one','required':True,'options':['Day','Night'],'personal_action':False}]}
    store.save_checkpoint(job.id,data)
    return app,store,job


def test_continue_after_restart_queues_only_valid_explicit_answers(tmp_path):
    app,store,job = setup_workspace(tmp_path)
    with TestClient(app) as client:
        url = '/api/jobs/' + job.id
        checkpoint = client.get(url+'/checkpoint')
        assert checkpoint.status_code == 200
        assert checkpoint.json()['field_details'][0]['options'] == ['Day','Night']
        assert client.post(url+'/continue',json={'confirmation':False,'answers':{'shift':'Night'}}).status_code == 409
        assert client.post(url+'/continue',json={'confirmation':True,'answers':{'shift':'Impossible'}}).status_code == 409
        response = client.post(url+'/continue',json={'confirmation':True,'answers':{'shift':'Night'}})
        assert response.status_code == 200
        assert store.job(job.id).status == 'queued'
        assert store.checkpoint(job.id)['confirmed']['step']['shift'] == 'Night'


def test_stale_checkpoint_cannot_be_approved_after_configuration_change(tmp_path):
    app,store,job = setup_workspace(tmp_path)
    config = store.config();config.profile.first_name='Changed';store.save_config(config)
    with TestClient(app) as client:
        result = client.post('/api/jobs/'+job.id+'/continue',json={'confirmation':True,'answers':{'shift':'Night'}})
        assert result.status_code == 409
        assert store.job(job.id).status == 'needs_input'
