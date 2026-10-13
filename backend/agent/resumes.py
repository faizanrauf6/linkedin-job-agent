import hashlib
import io
import re
import zipfile
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from .evidence import extract_identity_facts, identity_values
from .models import Profile, Resume

MAX_UPLOAD = 10 * 1024 * 1024


def import_resume(data: bytes, filename: str, label: str, runtime: Path) -> Resume:
    name = Path(filename.replace("\\", "/")).name
    extension = Path(name).suffix.lower()
    if len(data) > MAX_UPLOAD or not data:
        raise ValueError("Upload a nonempty CV under 10 MB")
    if extension not in {".pdf", ".docx"}:
        raise ValueError("Only PDF and DOCX CVs are supported")
    try:
        if extension == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise ValueError("Invalid PDF document")
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 30:
                raise ValueError("CV must be unencrypted and at most 30 pages")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(entry.file_size for entry in archive.infolist()) > 25 * 1024 * 1024:
                    raise ValueError("Document expands beyond the allowed size")
                if "word/document.xml" not in archive.namelist():
                    raise ValueError("Invalid Word document")
            document = Document(io.BytesIO(data))
            text = "\n".join([p.text for p in document.paragraphs] + [" ".join(c.text for c in row.cells) for t in document.tables for row in t.rows])
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The CV could not be read. Upload a valid PDF or DOCX.") from exc
    if len(text.strip()) < 20:
        raise ValueError("No readable CV text found. Image-only PDFs require a text-based replacement.")
    resume = Resume(label=label.strip() or Path(name).stem, filename=name, text=text[:100000], sha256=hashlib.sha256(data).hexdigest())
    resume.identity_facts = extract_identity_facts(resume.text)
    directory = runtime / "resumes"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (resume.id + extension)
    path.write_bytes(data)
    path.chmod(0o600)
    resume.path = str(path)
    return resume


def public_resume(resume: Resume):
    return resume.model_dump(exclude={"path"})


def assess_resume(resume: Resume, profile: Profile, peers: list[Resume]) -> list[str]:
    """Refresh explicit identity conflicts without recording raw identity values.

    This mutates the CV assessment, never confirms a CV or promotes extracted
    facts into the user profile. The caller persists it and gates confirmation.
    """
    resume.identity_facts = extract_identity_facts(resume.text)
    profile_facts = {key: getattr(profile, key) for key in ("email", "phone", "linkedin_url", "portfolio_url")}
    if profile.first_name.strip() and profile.last_name.strip():
        profile_facts["full_name"] = f"{profile.first_name} {profile.last_name}"
    comparisons = [("profile", profile_facts)]
    comparisons.extend(("CV " + peer.id, extract_identity_facts(peer.text)) for peer in sorted(peers, key=lambda p: p.id) if peer.id != resume.id)
    conflicts = []
    for source, facts in comparisons:
        for key in sorted(resume.identity_facts.keys() & facts.keys()):
            actual = identity_values(key, resume.identity_facts[key])
            other = identity_values(key, facts[key])
            if actual and other and actual.isdisjoint(other):
                # A digest invalidates acknowledgment when two different
                # mismatches would otherwise produce the same redacted message.
                digest = hashlib.sha256(repr((sorted(actual), sorted(other))).encode()).hexdigest()[:12]
                conflicts.append(f"{key} differs from {source} (evidence {digest})")
    if conflicts != resume.conflicts:
        resume.conflict_acknowledged = False
    resume.conflicts = conflicts
    return conflicts
