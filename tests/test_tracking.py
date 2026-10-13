import csv
import io
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.models import Job
from agent.store import Store
from agent.tracking import TrackingStore
from agent.tracking_api import register_tracking_routes


def workspace(tmp_path, **kwargs):
    store = Store(tmp_path / "agent.db")
    job = store.add_job(Job(linkedin_id="1", title="Platform Engineer", company="Example Systems", status="applying", adapter_name="lever", adapter_version="1", **kwargs))
    return store, job, TrackingStore(store)


def message(subject="Interview for Platform Engineer at Example Systems", body="Please schedule your interview.", sender="recruiter@example.com"):
    return f"From: {sender}\r\nTo: applicant@example.net\r\nSubject: {subject}\r\nMessage-ID: <private-message@example.com>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}".encode()


def test_attempts_and_observed_receipts_survive_restart_without_overwriting_history(tmp_path):
    store, job, tracking = workspace(tmp_path)
    first = tracking.start_attempt(job)
    tracking.finish_attempt(first, job, SimpleNamespace(state="failed", reason="Portal timeout", receipt={}, retryable=True))
    job.attempts += 1
    second = tracking.start_attempt(job)
    result = SimpleNamespace(state="submitted", reason="Visible confirmation", receipt={"text": "Thank you for applying", "reference": "APP-123", "url": "https://jobs.lever.co/example/confirm?token=private#secret", "observed_at": "2026-10-13T12:00:00+00:00"})
    tracking.finish_attempt(second, job, result)
    tracking.finish_attempt(second, job, result)
    history = TrackingStore(Store(store.path)).history(job.id)
    assert [attempt["state"] for attempt in history["attempts"]] == ["failed", "submitted"]
    assert history["attempts"][0]["adapter"] == "lever"
    assert history["attempts"][0]["version"] == "1"
    assert len(history["receipts"]) == 1
    assert history["receipts"][0]["reference"] == "APP-123"
    assert history["receipts"][0]["url"] == "https://jobs.lever.co/example/confirm"
    assert history["receipts"][0]["source"] == "observed_browser"
    assert store.job(job.id).status == "applying"


@pytest.mark.parametrize("state", ["needs_input", "failed", "submission_uncertain", "blocked"])
def test_unconfirmed_attempts_never_create_submission_receipts(tmp_path, state):
    _, job, tracking = workspace(tmp_path)
    attempt = tracking.start_attempt(job)
    tracking.finish_attempt(attempt, job, SimpleNamespace(state=state, reason="Unconfirmed", receipt={"text": "Thank you for applying", "reference": "APP-123"}))
    assert tracking.history(job.id)["receipts"] == []


def test_pipeline_stage_changes_have_durable_history_and_do_not_change_submission_state(tmp_path):
    store, job, tracking = workspace(tmp_path)
    tracking.set_stage(job.id, "interview", note="Recruiter called")
    tracking.set_stage(job.id, "offer", note="Written offer")
    history = TrackingStore(Store(store.path)).history(job.id)
    assert history["stage"] == "offer"
    assert [item["stage"] for item in history["stage_history"]] == ["interview", "offer"]
    assert history["stage_history"][0]["note"] == "Recruiter called"
    assert store.job(job.id).status == "applying"
    assert not store.job(job.id).submission_started
    with pytest.raises(ValueError):
        tracking.set_stage(job.id, "submitted")
    assert tracking.history(job.id)["stage"] == "offer"


def test_attempt_finalization_cannot_attach_another_jobs_receipt(tmp_path):
    store, job, tracking = workspace(tmp_path)
    attempt = tracking.start_attempt(job)
    other = store.add_job(Job(linkedin_id="2", title="Designer", company="Other"))
    with pytest.raises(ValueError):
        tracking.finish_attempt(attempt, other, SimpleNamespace(state="submitted", receipt={"text": "Confirmed"}))
    assert tracking.history(job.id)["attempts"][0]["state"] == "applying"
    assert tracking.history(other.id)["receipts"] == []


def test_email_import_requires_explicit_consent_and_existing_job(tmp_path):
    _, job, tracking = workspace(tmp_path)
    with pytest.raises(ValueError, match="authoriz|consent"):
        tracking.import_email(job.id, message())
    with pytest.raises(KeyError):
        tracking.import_email("missing", message(), authorized=True)
    assert tracking.history(job.id)["email_evidence"] == []


def test_imported_email_is_deduplicated_evidence_until_explicit_stage_acceptance(tmp_path):
    store, job, tracking = workspace(tmp_path)
    imported = tracking.import_email(job.id, message(), authorized=True)
    repeated = tracking.import_email(job.id, message(), authorized=True)
    assert repeated["id"] == imported["id"]
    assert repeated["duplicate"] is True
    history = TrackingStore(Store(store.path)).history(job.id)
    assert len(history["email_evidence"]) == 1
    assert history["email_evidence"][0]["suggested_stage"] == "interview"
    assert history["stage"] == "prospect"
    assert history["receipts"] == []
    assert store.job(job.id).status == "applying"
    tracking.set_stage(job.id, "interview", evidence_id=imported["id"])
    assert tracking.history(job.id)["stage_history"][0]["evidence_id"] == imported["id"]
    assert store.job(job.id).status == "applying"


@pytest.mark.parametrize("subject", ["Your application was received for Designer at Other Employer", "Your application was received for Designer at Example Systems", "Your application was received for Platform Engineer at Other Employer"])
def test_mismatched_receipt_email_remains_manual_evidence_and_never_counts_as_submitted(tmp_path, subject):
    store, job, tracking = workspace(tmp_path)
    imported = tracking.import_email(job.id, message(subject, "Thank you for applying. We received your application."), authorized=True)
    assert imported["classification"] == "receipt"
    assert imported["suggested_stage"] == ""
    with pytest.raises(ValueError):
        tracking.set_stage(job.id, "applied", evidence_id=imported["id"])
    assert tracking.history(job.id)["stage"] == "prospect"
    assert tracking.history(job.id)["receipts"] == []
    assert store.job(job.id).status == "applying"


def test_html_email_and_oversized_messages_are_not_executed_or_persisted_as_html(tmp_path):
    _, job, tracking = workspace(tmp_path)
    raw = b"From: sender@example.com\r\nSubject: Example Systems Platform Engineer\r\nContent-Type: text/html\r\n\r\n<script>secret()</script><img src='https://tracker.example/pixel'>"
    imported = tracking.import_email(job.id, raw, authorized=True)
    assert imported["text"] == ""
    assert "<script>" not in json.dumps(tracking.history(job.id))
    with pytest.raises(ValueError, match="size|large|limit"):
        tracking.import_email(job.id, b"x" * (1024 * 1024 + 1), authorized=True)


@pytest.mark.parametrize("dangerous", ["=HYPERLINK(\"https://bad.example\")", "+cmd", "-1+2", "@SUM(1)", "\t=1+1", "\n=1+1", "  =1+1", "\u00a0=1+1"])
def test_csv_export_escapes_formula_cells_and_keeps_complete_history(tmp_path, dangerous):
    store, job, tracking = workspace(tmp_path)
    job.title = dangerous
    store.save_job(job)
    attempt = tracking.start_attempt(job)
    tracking.finish_attempt(attempt, job, SimpleNamespace(state="failed", reason="Timeout", receipt={}))
    tracking.set_stage(job.id, "interview", note=dangerous)
    tracking.import_email(job.id, message(body=dangerous), authorized=True)
    rows = list(csv.DictReader(io.StringIO(tracking.export_csv(job.id))))
    assert {row["kind"] for row in rows} == {"attempt", "stage", "email"}
    assert all(row["job_title"].startswith("'") for row in rows)
    assert next(row for row in rows if row["kind"] == "stage")["note"].startswith("'")
    assert next(row for row in rows if row["kind"] == "email")["text"].startswith("'")
    exported = json.loads(tracking.export_json(job.id))
    assert exported["jobs"][0]["title"] == dangerous
    assert len(exported["jobs"][0]["history"]["attempts"]) == 1
    assert len(exported["jobs"][0]["history"]["email_evidence"]) == 1


def tracking_client(tmp_path):
    store, job, tracking = workspace(tmp_path)
    app = FastAPI()
    app.state.store = store
    register_tracking_routes(app, store, SimpleNamespace(busy=False), tmp_path)
    return TestClient(app), store, job, tracking


def test_tracking_routes_require_explicit_stage_confirmation_and_email_consent(tmp_path):
    client, store, job, tracking = tracking_client(tmp_path)
    path = "/api/jobs/" + job.id
    assert client.get(path + "/history").status_code == 200
    assert client.post(path + "/stage", json={"stage": "interview"}).status_code == 409
    assert client.post(path + "/stage", json={"stage": "interview", "confirmation": True}).status_code == 200
    assert client.post(path + "/email-evidence", files={"file": ("message.eml", message())}).status_code == 409
    result = client.post(path + "/email-evidence", data={"consent": "true"}, files={"file": ("message.eml", message())})
    assert result.status_code == 200
    assert store.job(job.id).status == "applying"
    assert client.get("/api/jobs/missing/history").status_code == 404
    assert client.get("/api/tracking/export?format=csv").headers["content-type"].startswith("text/csv")
    exported = client.get("/api/tracking/export?format=json&job_id=" + job.id)
    assert exported.status_code == 200
    assert exported.json()["jobs"][0]["history"]["stage"] == "interview"


def test_csv_export_includes_receipt_observation_and_email_match_metadata(tmp_path):
    _, job, tracking = workspace(tmp_path)
    attempt = tracking.start_attempt(job)
    tracking.finish_attempt(attempt, job, SimpleNamespace(state="submitted", receipt={"reference": "APP-123", "observed_at": "2026-10-13T12:00:00+00:00"}))
    tracking.import_email(job.id, message(subject="Interview for Designer at Other Company"), authorized=True)
    rows = list(csv.DictReader(io.StringIO(tracking.export_csv(job.id))))
    receipt = next(row for row in rows if row["kind"] == "receipt")
    email = next(row for row in rows if row["kind"] == "email")
    completed = next(row for row in rows if row["kind"] == "attempt")
    assert receipt["observed_at"] == "2026-10-13T12:00:00+00:00"
    assert receipt["attempt_id"] == attempt
    assert completed["finished_at"]
    assert email["company_match"] == "False"
    assert email["role_match"] == "False"
    assert email["trusted"] == "False"
    assert len(email["sha256"]) == 64


def test_mailbox_access_defaults_disabled_and_tracking_clear_cascades(tmp_path):
    client, store, job, tracking = tracking_client(tmp_path)
    response = client.get("/api/jobs/" + job.id + "/history")
    assert response.json()["email_integration"]["mailbox_access"] is False
    assert response.json()["email_integration"]["enabled"] is False
    attempt = tracking.start_attempt(job)
    tracking.finish_attempt(attempt, job, SimpleNamespace(state="submitted", receipt={"text": "Confirmed"}))
    tracking.set_stage(job.id, "interview")
    tracking.import_email(job.id, message(), authorized=True)
    store.clear()
    with store.connection() as db:
        for table in ("attempts", "receipts", "pipeline", "email_evidence"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
