"""Paused local backup/restore and value-free operational diagnostics."""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from .approvals import ApprovalStore
from .document_library import DocumentLibrary, verified_document_bytes
from .maintenance import MAX_ARCHIVE_BYTES, adapter_health, create_backup, restore_backup, sanitized_failure
from .store import Store
from .tracking import TrackingStore


def _initialize_restored_schema(runtime: Path):
    # Existing registrars keep Store objects which open this same path for each
    # operation. Recreate schemas without registering duplicate API routes.
    current = Store(runtime / "agent.db")
    library = DocumentLibrary(current, runtime)
    for document in library.documents():
        verified_document_bytes(document, runtime)
    ApprovalStore(current, runtime)
    TrackingStore(current)
    with current.connection() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS document_drafts (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS document_drafts_job ON document_drafts(job_id);
        """)
    current.recover()
    config = current.config()
    if not config.settings.paused:
        config.settings.paused = True
        current.save_config(config)


def _validate_restored_schema(archive: Path, preview: Path):
    # A structurally valid SQLite file may still contain an incompatible
    # additive table. Exercise migrations and document checks in isolation
    # before replacing the live database or closing its browser.
    restore_backup(preview, archive)
    _initialize_restored_schema(preview)


def register_maintenance_routes(app, store, worker, browser, runtime: Path):
    runtime = Path(runtime).resolve()

    def paused_idle(*, owns_lock=False):
        if worker.busy or (worker.run_lock.locked() and not owns_lock) or not store.config().settings.paused:
            raise HTTPException(409, "Pause the agent and wait for current work before backup or restore")

    @app.get("/api/maintenance/backup")
    async def backup():
        paused_idle()
        async with worker.run_lock:
            paused_idle(owns_lock=True)
            directory = Path(tempfile.mkdtemp(prefix="orbit-backup-")).resolve()
            try:
                archive = await asyncio.to_thread(create_backup, runtime, directory / "agent-backup.zip")
                return FileResponse(archive, media_type="application/zip", filename="agent-backup.zip", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}, background=BackgroundTask(shutil.rmtree, directory, ignore_errors=True))
            except (OSError, ValueError, sqlite3.Error) as exc:
                shutil.rmtree(directory, ignore_errors=True)
                raise HTTPException(409, "Backup could not be created. Check referenced documents and local permissions.") from exc
            except BaseException:
                shutil.rmtree(directory, ignore_errors=True)
                raise

    @app.post("/api/maintenance/restore")
    async def restore(file: UploadFile = File(...), confirmation: str = Form("")):
        directory = None
        try:
            paused_idle()
            if confirmation != "RESTORE BACKUP":
                raise HTTPException(409, "Type RESTORE BACKUP to confirm replacing this local workspace")
            if not (file.filename or "").lower().endswith(".zip"):
                raise HTTPException(400, "Choose a ZIP backup archive")
            async with worker.run_lock:
                paused_idle(owns_lock=True)
                raw = await file.read(MAX_ARCHIVE_BYTES + 1)
                paused_idle(owns_lock=True)
                if not raw or len(raw) > MAX_ARCHIVE_BYTES:
                    raise HTTPException(413, "Choose a nonempty backup archive no larger than 256 MB")
                documents = runtime / "documents"
                if documents.is_symlink() or documents.exists() and not documents.is_dir():
                    raise HTTPException(409, "The supporting document directory must be local and contain no symlinks")
                directory = Path(tempfile.mkdtemp(prefix="orbit-restore-")).resolve()
                archive = directory / "backup.zip"
                await asyncio.to_thread(archive.write_bytes, raw)
                paused_idle(owns_lock=True)
                try:
                    await asyncio.to_thread(_validate_restored_schema, archive, directory / "preview")
                except (ValueError, sqlite3.Error) as exc:
                    raise HTTPException(400, "Backup archive is invalid, damaged, or unsupported") from exc
                except OSError as exc:
                    raise HTTPException(503, "Backup validation could not finish. Check local disk permissions and available space.") from exc
                paused_idle(owns_lock=True)
                await browser.close()
                paused_idle(owns_lock=True)
                try:
                    report = await asyncio.to_thread(restore_backup, runtime, archive)
                except ValueError as exc:
                    raise HTTPException(400, "Backup archive is invalid, damaged, or unsupported") from exc
                except OSError as exc:
                    raise HTTPException(503, "Backup could not be installed. Check local disk permissions and available space.") from exc
                await asyncio.to_thread(_initialize_restored_schema, runtime)
                return report
        finally:
            await file.close()
            if directory is not None:
                shutil.rmtree(directory, ignore_errors=True)

    @app.get("/api/maintenance/adapters")
    def adapters():
        rows = []
        for value in adapter_health(store):
            attempts, submitted = value.get("attempts", 0), value.get("submitted", 0)
            rows.append({**value, "completion_ratio": submitted / attempts if attempts else 0.0})
        attempts = sum(row.get("attempts", 0) for row in rows)
        submitted = sum(row.get("submitted", 0) for row in rows)
        return {"adapters": rows, "total_attempts": attempts, "total_submitted": submitted, "completion_ratio": submitted / attempts if attempts else 0.0}

    @app.get("/api/jobs/{job_id}/diagnostics")
    def diagnostics(job_id: str):
        job = store.job(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        details = getattr(job, "field_details", None) or [{"label": question} for question in job.questions]
        artifact = sanitized_failure(job, missing_details=details)
        return Response(json.dumps(artifact, ensure_ascii=False, indent=2), media_type="application/json", headers={"Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="application-diagnostics.json"', "X-Content-Type-Options": "nosniff"})
