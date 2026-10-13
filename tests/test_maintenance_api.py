import asyncio
import hashlib
import io
import zipfile
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from agent.approval_api import register_approval_api
from agent.document_api import register_document_routes
from agent.document_library import DocumentLibrary
from agent.maintenance import create_backup, record_adapter_result
from agent.maintenance_api import register_maintenance_routes
from agent.models import Job, Resume
from agent.store import Store
from agent.tracking_api import register_tracking_routes
from agent.written_document_api import register_written_document_routes


class Browser:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


def workspace(runtime):
    store = Store(runtime / "agent.db")
    resume_file = runtime / "resumes" / "cv.pdf"
    resume_file.parent.mkdir(exist_ok=True)
    resume_file.write_bytes(b"%PDF-local original")
    resume = Resume(id="cv", label="CV", path=str(resume_file), filename="cv.pdf", confirmed=True, sha256=hashlib.sha256(resume_file.read_bytes()).hexdigest())
    store.save_resume(resume)
    job = store.add_job(Job(id="job1", linkedin_id="1", title="Engineer", company="Example", status="awaiting_review", resume_id="cv", resume_hash=resume.sha256, adapter_name="lever", adapter_version="1"))
    return store, job


def client_for(runtime, related=False):
    store, job = workspace(runtime)
    app = FastAPI()
    app.state.store = store
    worker = SimpleNamespace(busy=False, run_lock=asyncio.Lock())
    browser = Browser()
    if related:
        register_document_routes(app, store, worker, runtime)
        register_approval_api(app, store, worker, runtime)
        register_tracking_routes(app, store, worker, runtime)
        register_written_document_routes(app, store, worker, runtime)
    register_maintenance_routes(app, store, worker, browser, runtime)
    return TestClient(app), app, store, worker, browser, job


def test_backup_and_restore_routes_roundtrip_database_and_supporting_documents(tmp_path):
    runtime = tmp_path / "runtime"
    client, app, store, worker, browser, job = client_for(runtime, related=True)
    document = DocumentLibrary(store, runtime).import_document(b"Private supporting document", "letter.txt", "Letter", "other")
    job.status = "queued"
    store.save_job(job)
    (runtime / "browser").mkdir()
    (runtime / "browser" / "Cookies").write_bytes(b"cookie secret")
    response = client.get("/api/maintenance/backup")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(response.content)) as backup:
        assert "browser/Cookies" not in backup.namelist()
        assert "documents/" + document.id + ".txt" in backup.namelist()
    store.meta("after_backup", "discard this")
    result = client.post("/api/maintenance/restore", data={"confirmation": "RESTORE BACKUP"}, files={"file": ("backup.zip", response.content)})
    assert result.status_code == 200
    assert result.json()["restored"] is True
    assert store.meta("after_backup") == ""
    assert app.state.store.path == store.path
    assert browser.closed == 1
    assert store.config().settings.paused
    assert store.claim() is None
    assert store.application_held(job.id)
    restored = DocumentLibrary(store, runtime).documents()[0]
    assert restored.path == document.path
    assert Path(restored.path).read_bytes() == b"Private supporting document"
    assert not worker.run_lock.locked()


@pytest.mark.parametrize("guard", ["running", "busy", "locked"])
def test_backup_and_restore_refuse_nonidle_or_running_agent(tmp_path, guard):
    client, _, store, worker, browser, _ = client_for(tmp_path / "runtime")
    if guard == "running":
        config = store.config()
        config.settings.paused = False
        store.save_config(config)
    elif guard == "busy":
        worker.busy = True
    else:
        asyncio.run(worker.run_lock.acquire())
    assert client.get("/api/maintenance/backup").status_code == 409
    result = client.post("/api/maintenance/restore", data={"confirmation": "RESTORE BACKUP"}, files={"file": ("backup.zip", b"fake")})
    assert result.status_code == 409
    assert browser.closed == 0


def test_restore_requires_exact_confirmation_before_browser_or_runtime_changes(tmp_path):
    client, _, store, _, browser, _ = client_for(tmp_path / "runtime")
    original = store.path.read_bytes()
    result = client.post("/api/maintenance/restore", data={"confirmation": "restore backup"}, files={"file": ("backup.zip", b"fake")})
    assert result.status_code == 409
    assert store.path.read_bytes() == original
    assert browser.closed == 0


@pytest.mark.parametrize("attack", ["traversal", "tamper"])
def test_restore_rejects_malicious_archive_without_changing_database(tmp_path, attack):
    client, _, store, _, _, _ = client_for(tmp_path / "runtime")
    archive = create_backup(tmp_path / "runtime", tmp_path / "backup.zip")
    if attack == "traversal":
        with zipfile.ZipFile(archive, "a") as backup:
            backup.writestr("../escaped.txt", b"evil")
    else:
        with zipfile.ZipFile(archive) as backup:
            entries = [(info, backup.read(info)) for info in backup.infolist()]
        with zipfile.ZipFile(archive, "w") as backup:
            for info, data in entries:
                backup.writestr(info, b"tampered" if info.filename == "resumes/cv.pdf" else data)
    original = store.path.read_bytes()
    result = client.post("/api/maintenance/restore", data={"confirmation": "RESTORE BACKUP"}, files={"file": ("backup.zip", archive.read_bytes())})
    assert result.status_code == 400
    assert store.path.read_bytes() == original
    assert not (tmp_path / "escaped.txt").exists()


def test_restore_rechecks_paused_state_after_upload_read(tmp_path, monkeypatch):
    client, _, store, _, browser, _ = client_for(tmp_path / "runtime")
    archive = create_backup(tmp_path / "runtime", tmp_path / "backup.zip")
    read = UploadFile.read

    async def unpause_after_read(self, size=-1):
        content = await read(self, size)
        config = store.config()
        config.settings.paused = False
        store.save_config(config)
        return content

    monkeypatch.setattr(UploadFile, "read", unpause_after_read)
    response = client.post("/api/maintenance/restore", data={"confirmation": "RESTORE BACKUP"}, files={"file": ("backup.zip", archive.read_bytes())})
    assert response.status_code == 409
    assert browser.closed == 0


def test_restoring_old_schema_reinitializes_document_drafts_history_and_reviews(tmp_path):
    source = tmp_path / "legacy"
    old_store, old_job = workspace(source)
    archive = create_backup(source, tmp_path / "legacy.zip")
    client, _, _, _, _, _ = client_for(tmp_path / "runtime", related=True)
    response = client.post("/api/maintenance/restore", data={"confirmation": "RESTORE BACKUP"}, files={"file": ("backup.zip", archive.read_bytes())})
    assert response.status_code == 200
    assert client.get("/api/documents").json() == []
    assert client.get("/api/jobs/" + old_job.id + "/drafts").json() == []
    assert client.get("/api/jobs/" + old_job.id + "/history").status_code == 200
    assert client.get("/api/jobs/" + old_job.id + "/review").status_code == 200


def test_diagnostics_download_is_bounded_and_contains_no_answers_or_cv_values(tmp_path):
    client, _, store, _, _, job = client_for(tmp_path / "runtime")
    job.answers = {"Email": "private@example.com", "Full name": "Private Applicant"}
    job.questions = ["Email private@example.com", "Private Applicant"]
    job.error = "<html>password=secret Private CV content</html>"
    job.last_error_class = "UnsupportedField"
    store.save_job(job)
    response = client.get("/api/jobs/" + job.id + "/diagnostics")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    value = response.text
    for private in ["private@example.com", "Private Applicant", "Private CV content", "password=secret", "<html>"]:
        assert private not in value
    assert response.json()["error_class"] == "UnsupportedField"
    assert "answers" not in response.json()
    assert len(value) < 20000
    assert client.get("/api/jobs/missing/diagnostics").status_code == 404


def test_adapter_metrics_derive_completion_ratio_from_persisted_attempt_counts(tmp_path):
    client, _, store, _, _, job = client_for(tmp_path / "runtime")
    record_adapter_result(store, job, "submitted")
    record_adapter_result(store, job, "failed", "Timeout")
    response = client.get("/api/maintenance/adapters")
    assert response.status_code == 200
    metrics = response.json()
    assert metrics["completion_ratio"] == 0.5
    assert metrics["total_attempts"] == 2
    assert metrics["total_submitted"] == 1
    assert metrics["adapters"][0]["completion_ratio"] == 0.5
    assert metrics["adapters"][0]["errors"] == {"Timeout": 1}


def test_download_and_restore_remove_temporary_archives_after_use(tmp_path, monkeypatch):
    from agent import maintenance_api
    client, _, _, _, _, _ = client_for(tmp_path / "runtime")
    original = maintenance_api.tempfile.mkdtemp
    directories = []

    def track_directory(*args, **kwargs):
        path = original(*args, **kwargs)
        directories.append(Path(path))
        return path

    monkeypatch.setattr(maintenance_api.tempfile, "mkdtemp", track_directory)
    response = client.get("/api/maintenance/backup")
    assert response.status_code == 200
    assert directories and all(not path.exists() for path in directories)
    result = client.post("/api/maintenance/restore", data={"confirmation": "RESTORE BACKUP"}, files={"file": ("backup.zip", response.content)})
    assert result.status_code == 200
    assert all(not path.exists() for path in directories)


def test_restore_rejects_oversized_upload_before_closing_browser(tmp_path, monkeypatch):
    from agent import maintenance_api
    client, _, store, _, browser, _ = client_for(tmp_path / "runtime")
    original = store.path.read_bytes()
    monkeypatch.setattr(maintenance_api, "MAX_ARCHIVE_BYTES", 64)
    response = client.post("/api/maintenance/restore", data={"confirmation": "RESTORE BACKUP"}, files={"file": ("backup.zip", b"x" * 65)})
    assert response.status_code == 413
    assert browser.closed == 0
    assert store.path.read_bytes() == original


def test_restore_validates_additive_schema_before_replacing_runtime(tmp_path):
    source = tmp_path / "malformed"
    bad_store, _ = workspace(source)
    with bad_store.connection() as db:
        db.execute("CREATE TABLE approval_snapshots (id TEXT PRIMARY KEY,data TEXT)")
    archive = create_backup(source, tmp_path / "malformed.zip")
    client, _, store, _, browser, _ = client_for(tmp_path / "runtime", related=True)
    original = store.path.read_bytes()
    safe_client = TestClient(client.app, raise_server_exceptions=False)
    response = safe_client.post("/api/maintenance/restore", data={"confirmation": "RESTORE BACKUP"}, files={"file": ("backup.zip", archive.read_bytes())})
    assert response.status_code == 400
    assert store.path.read_bytes() == original
    assert browser.closed == 0


def test_restored_queued_application_requires_a_new_review_and_can_be_released(tmp_path):
    runtime = tmp_path / 'runtime'
    client, app, store, worker, browser, job = client_for(runtime, related=True)
    job.status = 'queued'
    job.approved_revision = store.config().revision
    store.save_job(job)
    previous_version = store.job(job.id).updated_at
    backup = client.get('/api/maintenance/backup')
    restored = client.post('/api/maintenance/restore', data={'confirmation':'RESTORE BACKUP'}, files={'file':('backup.zip',backup.content)})
    assert restored.status_code == 200
    current = store.job(job.id)
    assert current.status == 'awaiting_review'
    assert current.approved_revision == -1
    assert current.updated_at != previous_version
    assert store.application_held(job.id)
    review = client.get('/api/jobs/'+job.id+'/review')
    assert review.status_code == 200
    assert not review.json()['approved']
