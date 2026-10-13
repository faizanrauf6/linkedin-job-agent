"""Local supporting-document routes, registered by the main API application."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .document_library import DocumentKind, DocumentLibrary, MAX_DOCUMENT_BYTES, verified_document_bytes


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str | None = Field(default=None, min_length=1, max_length=200)
    kind: DocumentKind | None = None
    confirmed: bool | None = None


def register_document_routes(app, store, worker, runtime: Path) -> None:
    library = DocumentLibrary(store, runtime)

    def paused_idle(*, owns_lock: bool = False) -> None:
        if worker.busy or (worker.run_lock.locked() and not owns_lock) or not store.config().settings.paused:
            raise HTTPException(409, "Pause the agent and wait until it is idle before changing supporting documents")

    def public(record):
        return record.model_dump(exclude={"path"})

    def find(id: str):
        record = next((item for item in library.documents() if item.id == id), None)
        if record is None:
            raise HTTPException(404, "Supporting document not found")
        return record

    @app.get("/api/documents")
    async def documents():
        try:
            return [public(record) for record in library.documents()]
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/documents")
    async def upload_document(file: UploadFile = File(...), label: str = Form(""), kind: DocumentKind = Form("other")):
        try:
            paused_idle()
            async with worker.run_lock:
                paused_idle(owns_lock=True)
                data = await file.read(MAX_DOCUMENT_BYTES + 1)
                # Reading a spooled upload can yield. Check again before the
                # synchronous, bounded validation + mutation while holding lock.
                paused_idle(owns_lock=True)
                try:
                    return public(library.import_document(data, file.filename or "", label, kind))
                except (OSError, ValueError) as exc:
                    raise HTTPException(400, str(exc)) from exc
        finally:
            await file.close()

    @app.patch("/api/documents/{id}")
    async def update_document(id: str, body: DocumentUpdate):
        paused_idle()
        async with worker.run_lock:
            paused_idle(owns_lock=True)
            try:
                record = find(id)
                updated = record.model_copy(update=body.model_dump(exclude_none=True))
                return public(library.save(updated))
            except (OSError, ValueError) as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.delete("/api/documents/{id}")
    async def delete_document(id: str):
        paused_idle()
        async with worker.run_lock:
            paused_idle(owns_lock=True)
            try:
                library.delete(id)
            except KeyError as exc:
                raise HTTPException(404, "Supporting document not found") from exc
            except (OSError, ValueError) as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"ok": True}

    @app.get("/api/documents/{id}/file")
    async def download_document(id: str):
        try:
            record = find(id)
            data = verified_document_bytes(record, runtime)
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        media = {".pdf":"application/pdf", ".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".txt":"text/plain; charset=utf-8"}[Path(record.filename).suffix.lower()]
        return Response(data, media_type=media, headers={"Content-Disposition":"attachment; filename*=UTF-8''" + quote(record.filename, safe=""), "X-Content-Type-Options":"nosniff", "Cache-Control":"no-store"})
