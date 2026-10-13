import hashlib
import json
import os
import sqlite3
import stat
import zipfile
from contextlib import nullcontext
from pathlib import Path

import pytest

from agent.maintenance import adapter_health, create_backup, record_adapter_result, restore_backup, sanitized_failure
from agent.models import ApplyResult, Job, Resume
from agent.store import Store


def populated(runtime):
    store = Store(runtime / "agent.db")
    document = runtime / "resumes" / "cv.pdf"
    document.parent.mkdir()
    document.write_bytes(b"%PDF-private document")
    store.save_resume(Resume(id="cv", label="CV", path=str(document), sha256=hashlib.sha256(document.read_bytes()).hexdigest(), text="Private CV text"))
    for i, (status, started) in enumerate([("queued", False), ("applying", False), ("applying", True), ("submission_uncertain", True), ("submitted", True)]):
        store.add_job(Job(id=f"job{i}", linkedin_id=str(i), title=f"Engineer {i}", company="Example", status=status, submission_started=started, submission_at="2026-10-13T00:00:00+00:00" if started else "", resume_id="cv", adapter_name="lever", adapter_version="1"))
    with store.connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS checkpoints (job_id TEXT PRIMARY KEY, data TEXT)")
        db.execute("INSERT INTO checkpoints VALUES ('job2', '{\"step\":\"review\"}')")
        db.execute("CREATE TABLE outcome_history (id TEXT PRIMARY KEY, data TEXT)")
        db.execute("INSERT INTO outcome_history VALUES ('job4', '{\"state\":\"submitted\"}')")
    return store


def rewrite(archive, mutate):
    with zipfile.ZipFile(archive) as source:
        entries = [(info, source.read(info)) for info in source.infolist()]
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
        for info, data in entries:
            target.writestr(info, mutate(info.filename, data))


def test_backup_restores_documents_without_restoring_browser_or_queuing_interrupted_work(tmp_path):
    runtime = tmp_path / "original"
    populated(runtime)
    (runtime / "browser").mkdir()
    (runtime / "browser" / "Cookies").write_text("cookie secret")
    (runtime / "credentials.json").write_text("credential secret")
    archive = create_backup(runtime, tmp_path / "backup.zip")
    with zipfile.ZipFile(archive) as source:
        assert "browser/Cookies" not in source.namelist()
        assert "credentials.json" not in source.namelist()
        manifest = json.loads(source.read("manifest.json"))
        assert manifest["version"] == 1
        assert manifest["files"]["resumes/cv.pdf"]["sha256"] == hashlib.sha256(b"%PDF-private document").hexdigest()
    target = tmp_path / "restored"
    report = restore_backup(target, archive)
    restored = Store(target / "agent.db")
    restored.recover()
    assert report["restored"] is True
    assert restored.config().settings.paused
    assert restored.job("job1").status == "needs_input"
    assert restored.job("job2").status == "submission_uncertain"
    assert restored.job("job3").status == "submission_uncertain"
    assert restored.job("job4").status == "submitted"
    assert restored.job("job4").submission_at == "2026-10-13T00:00:00+00:00"
    assert restored.claim() is None
    assert Path(restored.resumes()[0].path) == target / "resumes" / "cv.pdf"
    assert Path(restored.resumes()[0].path).read_bytes() == b"%PDF-private document"
    with restored.connection() as db:
        assert json.loads(db.execute("SELECT data FROM checkpoints").fetchone()[0]) == {"step": "review"}
        assert json.loads(db.execute("SELECT data FROM outcome_history").fetchone()[0]) == {"state": "submitted"}


@pytest.mark.parametrize("attack", ["tamper", "traversal", "absolute", "backslash", "symlink", "duplicate", "bomb", "unknown", "bad_version"])
def test_invalid_archives_leave_existing_runtime_untouched(tmp_path, attack):
    source = tmp_path / "source"
    populated(source)
    archive = create_backup(source, tmp_path / "backup.zip")
    if attack == "tamper":
        rewrite(archive, lambda name, data: b"tampered" if name == "resumes/cv.pdf" else data)
    elif attack == "bad_version":
        rewrite(archive, lambda name, data: json.dumps({**json.loads(data), "version": 999}).encode() if name == "manifest.json" else data)
    else:
        with zipfile.ZipFile(archive, "a", zipfile.ZIP_DEFLATED) as target:
            if attack == "symlink":
                info = zipfile.ZipInfo("resumes/alias.pdf")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                target.writestr(info, "../../outside")
            else:
                names = {"traversal": "../escaped", "absolute": "/tmp/escaped", "backslash": "..\\escaped", "duplicate": "resumes/cv.pdf", "bomb": "resumes/bomb.pdf", "unknown": "unlisted.txt"}
                with pytest.warns(UserWarning) if attack == "duplicate" else nullcontext():
                    target.writestr(names[attack], b"0" * (2 * 1024 * 1024) if attack == "bomb" else b"evil")
    runtime = tmp_path / "target"
    current = populated(runtime)
    current.meta("sentinel", "do not alter")
    original = (runtime / "agent.db").read_bytes()
    with pytest.raises(ValueError):
        restore_backup(runtime, archive)
    assert (runtime / "agent.db").read_bytes() == original
    assert current.meta("sentinel") == "do not alter"
    assert (runtime / "resumes" / "cv.pdf").read_bytes() == b"%PDF-private document"
    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize("reference", ["outside", "symlink", "browser"])
def test_backup_refuses_document_paths_outside_confined_document_files(tmp_path, reference):
    runtime = tmp_path / "runtime"
    store = populated(runtime)
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"secret")
    if reference == "outside":
        path = secret
    elif reference == "symlink":
        path = runtime / "resumes" / "link.pdf"
        path.symlink_to(secret)
    else:
        path = runtime / "browser" / "credentials.pdf"
        path.parent.mkdir()
        path.write_bytes(b"secret")
    resume = store.resumes()[0]
    resume.path = str(path)
    store.save_resume(resume)
    with pytest.raises(ValueError):
        create_backup(runtime, tmp_path / "backup.zip")
    assert not (tmp_path / "backup.zip").exists()


def test_restore_refuses_existing_symlink_document_parent(tmp_path):
    source = tmp_path / "source"
    populated(source)
    archive = create_backup(source, tmp_path / "backup.zip")
    runtime = tmp_path / "runtime"
    Store(runtime / "agent.db")
    outside = tmp_path / "outside"
    outside.mkdir()
    (runtime / "resumes").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        restore_backup(runtime, archive)
    assert not (outside / "cv.pdf").exists()


def test_restore_refuses_symlink_sidecars_before_modifying_runtime(tmp_path):
    source = tmp_path / "source"
    populated(source)
    archive = create_backup(source, tmp_path / "backup.zip")
    runtime = tmp_path / "runtime"
    populated(runtime)
    original = (runtime / "agent.db").read_bytes()
    secret = tmp_path / "secret"
    secret.write_bytes(b"do not touch")
    (runtime / "agent.db-wal").write_bytes(b"preserve sidecar")
    (runtime / "agent.db-shm").symlink_to(secret)
    with pytest.raises(ValueError):
        restore_backup(runtime, archive)
    assert (runtime / "agent.db").read_bytes() == original
    assert secret.read_bytes() == b"do not touch"
    assert (runtime / "agent.db-wal").read_bytes() == b"preserve sidecar"


def test_backup_refuses_database_sidecars_outside_runtime(tmp_path):
    runtime = tmp_path / "runtime"
    populated(runtime)
    secret = tmp_path / "secret"
    secret.write_bytes(b"private sidecar")
    (runtime / "agent.db-wal").symlink_to(secret)
    with pytest.raises(ValueError):
        create_backup(runtime, tmp_path / "backup.zip")
    assert secret.read_bytes() == b"private sidecar"


def test_backup_captures_committed_wal_records_with_an_open_reader(tmp_path):
    runtime = tmp_path / "runtime"
    store = populated(runtime)
    with sqlite3.connect(store.path) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM jobs").fetchall()
        store.meta("last_commit", "present in WAL")
        assert Path(str(store.path) + "-wal").exists()
        archive = create_backup(runtime, tmp_path / "backup.zip")
    target = tmp_path / "target"
    restore_backup(target, archive)
    assert Store(target / "agent.db").meta("last_commit") == "present in WAL"


def test_backup_restores_additional_document_table_paths_portably(tmp_path):
    runtime = tmp_path / "runtime"
    store = populated(runtime)
    document = runtime / "documents" / "cover.txt"
    document.parent.mkdir()
    document.write_bytes(b"Private cover letter")
    with store.connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, data TEXT)")
        db.execute("INSERT INTO documents VALUES (?,?)", ("cover", json.dumps({"path": str(document), "sha256": hashlib.sha256(document.read_bytes()).hexdigest()})))
        db.execute("CREATE TABLE IF NOT EXISTS document_drafts (id TEXT PRIMARY KEY, data TEXT)")
        db.execute("INSERT INTO document_drafts VALUES (?,?)", ("draft", json.dumps({"answers": {"document_path": "Applicant confirmed answer"}})))
    archive = create_backup(runtime, tmp_path / "backup.zip")
    target = tmp_path / "restored"
    restore_backup(target, archive)
    with Store(target / "agent.db").connection() as db:
        saved = json.loads(db.execute("SELECT data FROM documents").fetchone()[0])
        draft = json.loads(db.execute("SELECT data FROM document_drafts").fetchone()[0])
    assert saved["path"] == str(target / "documents" / "cover.txt")
    assert draft["answers"]["document_path"] == "Applicant confirmed answer"
    assert Path(saved["path"]).read_bytes() == b"Private cover letter"


def test_restore_rolls_back_document_replacement_if_database_install_fails(tmp_path, monkeypatch):
    source = tmp_path / "source"
    populated(source)
    archive = create_backup(source, tmp_path / "backup.zip")
    target = tmp_path / "target"
    populated(target)
    (target / "resumes" / "cv.pdf").write_bytes(b"original document")
    original = (target / "agent.db").read_bytes()
    replace = os.replace

    def fail_database(source, destination):
        if Path(destination).name == "agent.db":
            raise OSError("injected disk failure")
        replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_database)
    with pytest.raises(OSError, match="disk failure"):
        restore_backup(target, archive)
    assert (target / "agent.db").read_bytes() == original
    assert (target / "resumes" / "cv.pdf").read_bytes() == b"original document"


def test_failure_artifact_keeps_form_shape_without_applicant_values_or_raw_content():
    secret = "Applicant Secret"
    job = Job(id="job1", linkedin_id="1", title="Engineer", company="Example", adapter_name="lever", adapter_version="1", last_error_class="UnsupportedField", answers={"Full name": secret, "Email": "private@example.com"}, description="Private CV text", error="<html>password=hunter2</html>")
    result = ApplyResult(state="needs_input", reason="private@example.com token=abc Private CV text", answers={"Password": "hunter2"})
    artifact = sanitized_failure(job, result, [{"label": "Email private@example.com", "type": "email", "options": ["Yes", "No", secret], "value": secret, "html": "<html>hunter2</html>", "step": "review"}])
    exported = json.dumps(artifact)
    for private in [secret, "private@example.com", "hunter2", "Private CV text", "token=abc", "<html>", "password="]:
        assert private not in exported
    assert artifact["job_id"] == "job1"
    assert artifact["adapter"] == "lever"
    assert artifact["version"] == "1"
    assert artifact["error_class"] == "UnsupportedField"
    assert artifact["fields"][0]["type"] == "email"
    assert artifact["fields"][0]["options"][:2] == ["Yes", "No"]
    assert len(artifact["reason"]) <= 300


def test_failure_artifact_redacts_unknown_applicant_text_in_labels_and_options():
    job = Job(id="job1", linkedin_id="1", title="Engineer", company="Example")
    artifact = sanitized_failure(job, missing_details=[{"label": "Private Applicant Name", "type": "select", "options": ["Yes", "No", "Private CV paragraph"]}])
    exported = json.dumps(artifact)
    assert "Private Applicant Name" not in exported
    assert "Private CV paragraph" not in exported
    assert artifact["fields"][0]["options"][:2] == ["Yes", "No"]


def test_adapter_health_persists_attempt_outcomes_and_separates_versions(tmp_path):
    store = Store(tmp_path / "agent.db")
    job = Job(id="job1", linkedin_id="1", title="Engineer", company="Example", adapter_name="lever", adapter_version="1")
    record_adapter_result(store, job, "submitted")
    record_adapter_result(store, job, "needs_input", "UnsupportedField")
    job.adapter_version = "2"
    record_adapter_result(store, job, "submission_uncertain", "PortalTimeout")
    rows = adapter_health(Store(tmp_path / "agent.db"))
    v1 = next(row for row in rows if row["version"] == "1")
    v2 = next(row for row in rows if row["version"] == "2")
    assert v1["adapter"] == "lever"
    assert v1["attempts"] == 2
    assert v1["submitted"] == 1
    assert v1["needs_input"] == 1
    assert v1["errors"] == {"UnsupportedField": 1}
    assert v2["submission_uncertain"] == 1
    assert v2["attempts"] == 1
    assert "Engineer" not in json.dumps(rows)


def test_backup_preserves_answers_named_path_without_interpreting_them_as_files(tmp_path):
    source = tmp_path / 'source'
    store = populated(source)
    job = store.jobs()[0]
    job.answers = {'path':'Night shift','portfolio_path':'Applicant confirmed answer'}
    job.answer_sources = {'path':'confirmed by applicant'}
    store.save_job(job)
    archive = create_backup(source,tmp_path/'backup.zip')
    target = tmp_path/'restored'
    restore_backup(target,archive)
    restored = Store(target/'agent.db').job(job.id)
    assert restored.answers == job.answers
    assert restored.answer_sources == job.answer_sources
