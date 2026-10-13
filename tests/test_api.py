import io

import pytest
from fastapi.testclient import TestClient
from docx import Document

from agent.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, run_worker=False)) as client:
        yield client


def test_config_persists_and_stale_edits_are_rejected(client):
    config = client.get("/api/config").json()
    config["profile"]["first_name"] = "Jhon"
    saved = client.put("/api/config", json=config)
    assert saved.status_code == 200
    assert client.get("/api/config").json()["profile"]["first_name"] == "Jhon"
    assert client.put("/api/config", json=config).status_code == 409


def test_cross_origin_mutations_are_rejected(client):
    response = client.post("/api/control", json={"action": "resume"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert client.get("/api/config").json()["settings"]["paused"]


def test_bad_limits_are_rejected(client):
    config = client.get("/api/config").json()
    config["settings"]["daily_limit"] = -10
    assert client.put("/api/config", json=config).status_code == 422


def test_upload_extracts_cv_and_requires_confirmation(client):
    document = Document()
    document.add_paragraph("React TypeScript frontend engineer")
    buffer = io.BytesIO()
    document.save(buffer)
    response = client.post("/api/resumes", files={"file": ("../../frontend.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}, data={"label": "Frontend"})
    assert response.status_code == 200
    cv = response.json()
    assert cv["filename"] == "frontend.docx"
    assert not cv["confirmed"]
    assert "React TypeScript" in cv["text"]
    assert "path" not in cv
    assert client.patch("/api/resumes/" + cv["id"], json={"confirmed": True, "skills": ["React"]}).status_code == 200


def test_upload_rejects_executable_or_fake_document(client):
    assert client.post("/api/resumes", files={"file": ("script.py", b"print(1)")}).status_code == 400
    assert client.post("/api/resumes", files={"file": ("fake.pdf", b"not a pdf")}).status_code == 400


def test_agent_starts_paused_and_local_data_can_be_cleared(client):
    assert client.get("/api/status").json()["paused"]
    assert client.request("DELETE", "/api/data", json={"confirmation": "DELETE LOCAL DATA"}).status_code == 200
    assert client.get("/api/resumes").json() == []


def test_delete_local_data_refuses_an_operation_holding_the_worker_lock(client):
    worker = client.app.state.worker
    client.portal.call(worker.run_lock.acquire)
    try:
        response = client.request('DELETE','/api/data',json={'confirmation':'DELETE LOCAL DATA'})
        assert response.status_code == 409
    finally:
        client.portal.call(worker.run_lock.release)
