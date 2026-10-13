from fastapi.testclient import TestClient
from agent.api import create_app


def test_structured_applicant_records_round_trip_through_settings_api(tmp_path):
    with TestClient(create_app(tmp_path,run_worker=False)) as client:
        config=client.get('/api/config').json()
        config['profile']['records']={'employment':[{'organization':'Clinic','title':'Registered nurse','start_date':'2020-01-01','current':True}],
            'licenses':[{'name':'Registered nurse','issuer':'Nursing board','identifier':'RN-123'}],
            'languages':[{'name':'English','proficiency':'fluent'}]}
        result=client.put('/api/config',json=config)
        assert result.status_code==200
        saved=client.get('/api/config').json()['profile']['records']
        assert saved['employment'][0]['organization']=='Clinic'
        assert saved['licenses'][0]['identifier']=='RN-123'
