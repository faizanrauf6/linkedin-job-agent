"""Local explicit-consent routes for manual read-only IMAP connections."""
import asyncio

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .email_connector import EmailConnector


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(max_length=253)
    username: str = Field(max_length=254)
    secret: SecretStr
    folder: str = Field(default="INBOX", max_length=200)
    port: int = 993
    confirmation: bool = False


class MailConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: bool = False


def register_email_routes(app, store, worker, runtime):
    connector = EmailConnector(store)

    def paused_idle(*, owns_lock=False):
        if worker.busy or (worker.run_lock.locked() and not owns_lock) or not store.config().settings.paused:
            raise HTTPException(409, "Pause the agent and wait for current work before changing or syncing a mailbox")

    def consent(confirmed):
        if confirmed is not True:
            raise HTTPException(409, "Explicit read-only mailbox authorization is required")

    @app.get("/api/email/connection")
    def status():
        return connector.status()

    @app.post("/api/email/connection")
    async def configure(body: ConnectionRequest):
        paused_idle()
        consent(body.confirmation)
        async with worker.run_lock:
            paused_idle(owns_lock=True)
            try:
                return await asyncio.to_thread(connector.configure, body.host, body.username, body.secret.get_secret_value(), True, body.port, body.folder)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/email/connection")
    async def disconnect(body: MailConsent):
        paused_idle()
        consent(body.confirmation)
        async with worker.run_lock:
            paused_idle(owns_lock=True)
            try:
                return await asyncio.to_thread(connector.disconnect, True)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

    @app.post("/api/jobs/{job_id}/email-sync")
    async def sync(job_id: str, body: MailConsent):
        paused_idle()
        consent(body.confirmation)
        if store.job(job_id) is None:
            raise HTTPException(404, "Job not found")
        async with worker.run_lock:
            paused_idle(owns_lock=True)
            try:
                return await asyncio.to_thread(connector.sync, job_id, True)
            except KeyError as exc:
                raise HTTPException(404, "Job not found") from exc
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
