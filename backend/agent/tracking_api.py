"""Tracking routes do not queue applications, contact mailboxes, or submit forms."""
from pathlib import Path

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .tracking import MAX_EMAIL_BYTES, STAGES, TrackingStore
from .email_connector import EmailConnector


class StageChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str
    confirmation: bool = False
    note: str = Field(default="", max_length=1000)
    evidence_id: str = Field(default="", max_length=80)


def register_tracking_routes(app, store, worker, runtime: Path):
    # Recreate additive tables after restoration of an older backup as needed.
    def tracking():
        return TrackingStore(getattr(app.state, "store", store))

    def existing(job_id):
        current = getattr(app.state, "store", store)
        if current.job(job_id) is None:
            raise HTTPException(404, "Job not found")

    tracking()

    @app.get("/api/jobs/{job_id}/history")
    def history(job_id: str):
        existing(job_id)
        return {**tracking().history(job_id), "stages": STAGES, "email_integration": {"enabled": EmailConnector(store).status()["enabled"], "mode": "explicit_eml_import_or_opt_in_imap", "automatic_polling": False, "mailbox_access": False}}

    @app.post("/api/jobs/{job_id}/stage")
    def stage(job_id: str, body: StageChange):
        existing(job_id)
        if not body.confirmation:
            raise HTTPException(409, "Explicitly confirm this tracking stage change")
        try:
            return tracking().set_stage(job_id, body.stage, body.note, body.evidence_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/jobs/{job_id}/email-evidence")
    async def email(job_id: str, file: UploadFile = File(...), consent: bool = Form(False)):
        existing(job_id)
        if not consent:
            raise HTTPException(409, "Explicit consent is required to import this email")
        if not (file.filename or "").lower().endswith(".eml"):
            raise HTTPException(400, "Choose an .eml email file")
        raw = await file.read(MAX_EMAIL_BYTES + 1)
        try:
            return tracking().import_email(job_id, raw, authorized=True)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            await file.close()

    @app.get("/api/tracking/export")
    def export(format: str = "json", job_id: str | None = None):
        if format not in {"csv", "json"}:
            raise HTTPException(400, "Choose csv or json export")
        if job_id:
            existing(job_id)
        records = tracking()
        value = records.export_csv(job_id) if format == "csv" else records.export_json(job_id)
        return Response(value, media_type="text/csv" if format == "csv" else "application/json", headers={"Content-Disposition": f'attachment; filename="application-history.{format}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
