"""Original supporting documents; never infer profile facts from uploaded files."""
from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from docx import Document
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator
from pypdf import PdfReader

from .models import now

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 25 * 1024 * 1024
MAX_DOCUMENT_PAGES = 100
MAX_ARCHIVE_ENTRIES = 1000
DocumentKind = Literal["cover_letter", "certificate", "license", "portfolio", "other"]
EXTENSIONS = {".pdf", ".docx", ".txt"}


class DocumentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    label: str = Field(min_length=1, max_length=200)
    kind: DocumentKind
    filename: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: bool = False
    created_at: str = Field(default_factory=now)
    _runtime_root: Path | None = PrivateAttr(default=None)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if "\x00" in value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("Document filename must be a basename")
        if Path(value).suffix.lower() not in EXTENSIONS:
            raise ValueError("Only PDF, DOCX and TXT documents are supported")
        return value


def _directory(runtime: Path, *, create: bool = False) -> Path:
    root = Path(runtime)
    if root.is_symlink():
        raise ValueError("Document runtime must not be a symlink")
    root = root.resolve()
    directory = root / "documents"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ValueError("Document directory must be a local directory without symlinks")
    if create:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    return directory


def _confined_path(record: DocumentRecord, runtime: Path | None = None, *, metadata_only: bool = False) -> Path:
    root = runtime if runtime is not None else record._runtime_root
    if root is None:
        raise ValueError("Document must be loaded from its local library")
    directory = _directory(root)
    expected = directory / (record.id + Path(record.filename).suffix.lower())
    supplied = Path(record.path)
    if not supplied.is_absolute() or supplied != expected:
        raise ValueError("Document original must stay inside its local document library")
    if not metadata_only and (supplied.is_symlink() or supplied.resolve() != expected):
        raise ValueError("Document original path contains a symlink")
    return expected


@contextmanager
def _opened_directory(runtime: Path):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_descriptor = os.open(runtime, flags)
        try:
            directory_descriptor = os.open("documents", flags, dir_fd=root_descriptor)
            try:
                yield directory_descriptor
            finally:
                os.close(directory_descriptor)
        finally:
            os.close(root_descriptor)
    except OSError as exc:
        raise ValueError("Document directory is unavailable or contains a symlink") from exc


def verified_document_bytes(record: DocumentRecord, runtime: Path | None = None) -> bytes:
    """Read one bounded regular file without following a replaced final symlink."""
    path = _confined_path(record, runtime)
    try:
        root = runtime if runtime is not None else record._runtime_root
        with _opened_directory(root) as directory_descriptor:
            descriptor = os.open(path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=directory_descriptor)
            with os.fdopen(descriptor, "rb") as original:
                metadata = os.fstat(original.fileno())
                if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_DOCUMENT_BYTES:
                    raise ValueError("Document original is missing or exceeds the allowed size")
                data = original.read(MAX_DOCUMENT_BYTES + 1)
    except OSError as exc:
        raise ValueError("Document original is unavailable") from exc
    if len(data) > MAX_DOCUMENT_BYTES or hashlib.sha256(data).hexdigest() != record.sha256:
        raise ValueError("Document original changed; import and confirm the replacement")
    return data


def verified_document_path(record: DocumentRecord, runtime: Path | None = None) -> Path:
    verified_document_bytes(record, runtime)
    return _confined_path(record, runtime)


def _validate_document(data: bytes, extension: str) -> None:
    if not data or len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("Upload a nonempty document up to 10 MB")
    if extension not in EXTENSIONS:
        raise ValueError("Only PDF, DOCX and TXT documents are supported")
    try:
        if extension == ".txt":
            text = data.decode("utf-8-sig")
            if not text.strip() or any((ord(char) < 32 and char not in "\n\r\t") or 127 <= ord(char) <= 159 for char in text):
                raise ValueError("TXT files must contain readable UTF-8 text")
        elif extension == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise ValueError("Invalid PDF signature")
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValueError("Supporting documents must be unencrypted")
            count = reader.trailer["/Root"]["/Pages"].get("/Count", 0)
            if not 1 <= count <= MAX_DOCUMENT_PAGES or not 1 <= len(reader.pages) <= MAX_DOCUMENT_PAGES:
                raise ValueError("Supporting PDFs must have 1 to 100 pages")
            # Do not extract text: an image-only certificate is a valid original,
            # and its content must never silently become an applicant claim.
        else:
            if not data.startswith(b"PK\x03\x04"):
                raise ValueError("Invalid DOCX signature")
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_ARCHIVE_ENTRIES or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                    raise ValueError("Word document expands beyond the allowed bounds")
                names = [entry.filename for entry in entries]
                if len(names) != len(set(names)) or not {"word/document.xml", "[Content_Types].xml", "_rels/.rels"}.issubset(names):
                    raise ValueError("Invalid Word document structure")
                if any(entry.flag_bits & 1 or entry.filename.startswith("/") or ".." in Path(entry.filename.replace("\\", "/")).parts for entry in entries):
                    raise ValueError("Invalid Word document archive")
                if any("vbaproject" in name.casefold() or name.casefold().startswith("word/embeddings/") for name in names):
                    raise ValueError("Use a DOCX without macros or embedded files")
            Document(io.BytesIO(data))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The document could not be read; upload a valid PDF, DOCX or UTF-8 TXT file") from exc


class DocumentLibrary:
    def __init__(self, store, runtime: Path):
        self.store = store
        self.runtime = Path(runtime).resolve()
        self.directory = _directory(runtime, create=True)
        with store.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    @staticmethod
    def _bump_generation(db) -> None:
        row = db.execute("SELECT value FROM meta WHERE key='document_generation'").fetchone()
        generation = int(row[0]) if row and row[0].isdigit() else 0
        db.execute("INSERT INTO meta VALUES ('document_generation',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(generation + 1),))

    def _bind(self, record: DocumentRecord) -> DocumentRecord:
        record._runtime_root = self.runtime
        _confined_path(record, self.runtime, metadata_only=True)
        return record

    def documents(self) -> list[DocumentRecord]:
        with self.store.connection() as db:
            return [self._bind(DocumentRecord.model_validate_json(row[0])) for row in db.execute("SELECT data FROM documents ORDER BY rowid DESC")]

    def import_document(self, data: bytes, filename: str, label: str, kind: DocumentKind) -> DocumentRecord:
        name = Path(filename.replace("\\", "/")).name
        extension = Path(name).suffix.lower()
        _validate_document(data, extension)
        record = DocumentRecord(label=label.strip() or Path(name).stem, kind=kind, filename=name,
                                path=str(self.directory / "pending"), sha256=hashlib.sha256(data).hexdigest())
        record.path = str(self.directory / (record.id + extension))
        self._bind(record)
        # Exclusive creation protects existing originals even if IDs collide.
        with _opened_directory(self.runtime) as directory_descriptor:
            filename = Path(record.path).name
            descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory_descriptor)
            try:
                with os.fdopen(descriptor, "wb") as original:
                    original.write(data)
                return self.save(record)
            except BaseException:
                try:
                    os.unlink(filename, dir_fd=directory_descriptor)
                except FileNotFoundError:
                    pass
                raise


    def save(self, record: DocumentRecord) -> DocumentRecord:
        record = self._bind(DocumentRecord.model_validate(record.model_dump()))
        verified_document_bytes(record, self.runtime)
        with self.store.connection() as db:
            existing = db.execute("SELECT data FROM documents WHERE id=?", (record.id,)).fetchone()
            if existing:
                prior = DocumentRecord.model_validate_json(existing[0])
                if any(getattr(prior, field) != getattr(record, field) for field in ("filename", "path", "sha256", "created_at")):
                    raise ValueError("Import a replacement instead of changing an original document")
                if prior.model_dump() == record.model_dump():
                    return record
            db.execute("INSERT INTO documents VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (record.id, record.model_dump_json()))
            self._bump_generation(db)
        return record

    def delete(self, id: str) -> None:
        with self.store.connection() as db:
            row = db.execute("SELECT data FROM documents WHERE id=?", (id,)).fetchone()
            if not row:
                raise KeyError(id)
            record = self._bind(DocumentRecord.model_validate_json(row[0]))
            path = _confined_path(record, self.runtime)
            with _opened_directory(self.runtime) as directory_descriptor:
                try:
                    os.unlink(path.name, dir_fd=directory_descriptor)
                except FileNotFoundError:
                    pass
            db.execute("DELETE FROM documents WHERE id=?", (id,))
            self._bump_generation(db)


def _field_key(value: str) -> str:
    key = re.sub(r"[\W_]+", " ", value.casefold()).strip()
    key = re.sub(r"^(?:please )?(?:upload|attach|select|choose)(?: your| a| an| the)? ", "", key)
    return re.sub(r"\s+(?:required|optional)$", "", key).strip()


def document_for_field(label: str, documents: list[DocumentRecord]) -> DocumentRecord | None:
    """Select only one confirmed original, with no generic attachment fallback."""
    key = _field_key(label)
    if re.search(r"\b(?:cv|resume|résumé|curriculum vitae)\b", key):
        return None
    confirmed = [record for record in documents if isinstance(record, DocumentRecord) and record.confirmed
                 and not re.search(r"\b(?:cv|resume|résumé|curriculum vitae)\b", _field_key(record.label + " " + Path(record.filename).stem))]
    exact = [record for record in confirmed if _field_key(record.label) == key]
    if exact:
        candidates = exact
    else:
        kinds = {
            "cover letter":"cover_letter", "covering letter":"cover_letter",
            "certificate":"certificate", "certification":"certificate", "professional certificate":"certificate",
            "license":"license", "licence":"license", "professional license":"license", "professional licence":"license",
            "portfolio":"portfolio", "portfolio document":"portfolio",
        }
        kind = kinds.get(key)
        candidates = [record for record in confirmed if record.kind == kind] if kind else []
    if len(candidates) != 1:
        return None
    try:
        verified_document_path(candidates[0])
    except (OSError, ValueError):
        return None
    return candidates[0]
