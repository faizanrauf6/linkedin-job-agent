"""Local extraction and redaction of explicitly supplied CV evidence."""
import re
from urllib.parse import urlsplit

from .models import Profile, Resume

EMAIL = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9.-]*[A-Z0-9])?\.[A-Z]{2,}", re.I)
URL = re.compile(r"https?://[^\s<>]+", re.I)
PHONE = re.compile(r"(?<!\w)\+?\d[\d ()-]{6,}\d(?!\w)")
SENSITIVE_DETAIL = re.compile(r"\b(?:date of birth|birth date|dob|passport|national id|social security|ssn|marital status|religion|nationality|citizenship|gender|ethnicity|disability|veteran|sexual orientation|pronouns)\b", re.I)
SENSITIVE_LINE = re.compile(r"^\s*(?:full name|name|first name|last name|e-?mail|phone|mobile|telephone|tel|contact|address|date of birth|birth date|dob|age|passport|national id|social security|ssn|religion|marital status|gender|citizenship|nationality)\s*[:=]", re.I)


def extract_identity_facts(text: str) -> dict[str, str]:
    """Only labeled names and clear contact lines; never guess a header's owner."""
    found: dict[str, list[str]] = {}

    def add(key, value):
        value = value.strip()
        if value and value not in found.setdefault(key, []):
            found[key].append(value)

    for line in text.splitlines():
        line = line.strip()
        name = re.fullmatch(r"(?:full name|name)\s*[:=]\s*([^\d:@|<>]+)", line, re.I)
        if name and 1 < len(name[1].split()) <= 6 and len(name[1]) <= 100:
            add("full_name", name[1])
        # A line consisting only of contact tokens and separators is explicit;
        # an employer email embedded in work prose is not an identity fact.
        remainder = PHONE.sub("", URL.sub("", EMAIL.sub("", line)))
        contact_line = bool(EMAIL.search(line) or URL.search(line)) and not remainder.strip(" |,;•·\t")
        email_line = contact_line or re.match(r"^(?:e-?mail(?: address)?|contact)\s*[:=]", line, re.I) or EMAIL.fullmatch(line)
        if email_line:
            for email in EMAIL.findall(line):
                add("email", email)
        phone_line = contact_line or re.match(r"^(?:phone(?: number)?|mobile(?: phone)?|telephone|tel|contact)\s*[:=]", line, re.I) or re.fullmatch(r"\+[\d ()-]+", line)
        if phone_line:
            for phone in PHONE.findall(line):
                if 8 <= len(re.sub(r"\D", "", phone)) <= 15:
                    add("phone", phone)
        for url in URL.findall(line):
            try:
                parsed = urlsplit(url.rstrip(".,;"))
            except ValueError:
                continue
            if parsed.hostname and parsed.hostname.casefold().removeprefix("www.") == "linkedin.com" and re.match(r"^/(?:in|pub)/[^/]+", parsed.path, re.I):
                if contact_line or re.match(r"^(?:linkedin(?: profile)?(?: url)?\s*[:=]\s*)?https?://", line, re.I):
                    add("linkedin_url", url.rstrip(".,;"))
            elif re.match(r"^(?:portfolio|website|personal website)(?: url)?\s*[:=]", line, re.I):
                add("portfolio_url", url.rstrip(".,;"))
    return {key: "\n".join(values) for key, values in found.items()}


def identity_values(key: str, value: str) -> set[str]:
    """Comparable explicit values; missing values remain unknown."""
    values = set()
    for part in value.splitlines():
        part = part.strip().casefold()
        if key == "phone":
            part = re.sub(r"\D", "", part)
        elif key.endswith("_url"):
            try:
                parsed = urlsplit(part if "://" in part else "https://" + part)
            except ValueError:
                continue
            part = (parsed.hostname or "").removeprefix("www.") + parsed.path.rstrip("/")
        elif key == "full_name":
            part = " ".join(part.split())
        if part:
            values.add(part)
    return values


def redact_evidence(text: str, profile: Profile, resume: Resume | None = None) -> str:
    """Remove contact/identity details before sending any prose to a provider."""
    identity = extract_identity_facts(resume.text) if resume else {}
    private = [profile.email, profile.phone, profile.linkedin_url, profile.portfolio_url, profile.location]
    if profile.first_name and profile.last_name:
        private.append(f"{profile.first_name} {profile.last_name}")
    private.extend(v for values in identity.values() for v in values.splitlines())
    lines = []
    for line in text.splitlines():
        if SENSITIVE_LINE.match(line) or SENSITIVE_DETAIL.search(line):
            continue
        for value in sorted(set(private), key=len, reverse=True):
            if value.strip():
                line = re.sub(re.escape(value.strip()), "[redacted]", line, flags=re.I)
        line = EMAIL.sub("[redacted]", line)
        line = URL.sub("[redacted]", line)
        line = PHONE.sub("[redacted]", line)
        if line.strip() and re.search(r"[A-Za-z]", line.replace("[redacted]", "")):
            lines.append(line.strip())
    return "\n".join(lines)



def cv_claim(line: str) -> bool:
    """Conservatively omit ambiguous headings, names and postal-address lines."""
    return bool(re.search(r"\b(?:built|developed|implemented|designed|led|managed|created|delivered|worked|maintained|improved|launched|supported|achieved|automated|collaborated|contributed|engineer|developer|architect|analyst|designer|scientist|manager)\b", line, re.I) or re.match(r"^(?:skills|experience|education|qualifications|certifications|degree|role|technologies|projects)\s*:", line, re.I))
