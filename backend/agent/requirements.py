"""Extract explicit mandatory qualifications and assess only confirmed facts.

This intentionally uses a small, inspectable grammar. A missing record is not
proof that an applicant lacks a qualification, and alternative qualifications
remain alternatives rather than turning into independent requirements.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

from .profile_records import ApplicantRecords

if TYPE_CHECKING:
    from .models import Profile


@dataclass(frozen=True)
class Requirement:
    kind: Literal["license", "certification", "degree", "language", "experience", "skill"]
    name: str
    evidence: str
    alternatives: tuple[str, ...] = ()
    minimum: float | None = None
    proficiency: str = ""
    field_of_study: str = ""
    current_required: bool = False


@dataclass(frozen=True)
class RequirementAssessment:
    requirement: Requirement
    status: Literal["met", "unmet", "unknown"]
    reason: str
    sources: tuple[str, ...] = ()


CREDENTIAL_ALIASES = {
    "registered nurse": "registered nurse", "rn": "registered nurse",
    "lpn": "licensed practical nurse", "lvn": "licensed vocational nurse",
    "cdl": "commercial driver license", "commercial driver s": "commercial driver",
    "bls": "basic life support", "acls": "advanced cardiovascular life support",
    "cpr": "cardiopulmonary resuscitation", "pals": "pediatric advanced life support",
}
DEGREE_LEVELS = {"bachelor": 2, "bachelors": 2, "bsc": 2, "bs": 2, "ba": 2, "bsn": 2,
                 "master": 3, "masters": 3, "msc": 3, "ms": 3, "ma": 3,
                 "doctorate": 4, "doctoral": 4, "phd": 4, "associate": 1, "associates": 1}
PROFICIENCY = {"basic": 1, "conversational": 2, "professional": 3, "fluent": 4, "native": 5}
LANGUAGES = "English|Spanish|French|German|Urdu|Arabic|Hindi|Punjabi|Portuguese|Russian|Mandarin|Cantonese|Chinese|Japanese|Korean|Italian|Bengali|Turkish|Polish|Dutch|Swedish|Norwegian|Danish|Vietnamese|Tagalog|Filipino|Tamil|Telugu|Malay|Indonesian|Swahili|Pashto|Persian|Farsi"
NUMBER_WORDS = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,"fifteen":15,"twenty":20}
MANDATORY = r"\b(?:must|required|mandatory|essential|minimum|at least)\b"
OPTIONAL = r"\b(?:preferred|desirable|optional|a plus|nice to have|not required)\b"


def qualification_key(value: str) -> str:
    text = re.sub(r"[^\w+#]+", " ", value.casefold()).strip()
    text = re.sub(r"\blicences?\b", "license", text)
    for alias in sorted(CREDENTIAL_ALIASES, key=len, reverse=True):
        text = re.sub(r"\b" + re.escape(alias) + r"\b", CREDENTIAL_ALIASES[alias], text)
    text = re.sub(r"\b(?:valid|active|current|license|licenses|certification|certificate|registration)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_name(value: str) -> str:
    value = value.strip(" .,:;-•*")
    value = re.sub(r"^(?:(?:must|candidates?|applicants?|you)\s+)*(?:(?:have|hold|possess|maintain|require|requires|need|needs|be|a|an|the|either|valid|current|active)\s+)+", "", value, flags=re.I)
    return value.strip()


def degree_level(value: str) -> int | None:
    text = re.sub(r"[^\w]+", " ", value.casefold()).strip()
    for token, level in DEGREE_LEVELS.items():
        if re.search(r"\b" + token + r"\b", text):
            return level
    if re.search(r"\bbachelor\s+s\b", text):
        return 2
    if re.search(r"\bmaster\s+s\b", text):
        return 3
    return None


def _qualification_clauses(description: str):
    for raw in re.split(r"(?<=[.!?])\s+|;\s*|\n+", description):
        evidence = raw.strip()
        # Shared leading "must" can govern a conjunction of credentials, but
        # preferred/required modifiers stay with their own qualification.
        shared_mandatory = bool(re.search(MANDATORY, raw, re.I)) and not re.search(OPTIONAL, raw, re.I)
        delimiter = r"\s+(?:but|whereas|while)\s+|(?<=required)\s+and\s+|(?<=preferred)\s+and\s+|(?<=license)\s+and\s+|(?<=licence)\s+and\s+|(?<=certification)\s+and\s+"
        if re.search(OPTIONAL, raw, re.I) and re.search(MANDATORY, raw, re.I):
            delimiter += r"|,\s*"
        for part in re.split(delimiter, raw, flags=re.I):
            yield part.strip(), evidence, shared_mandatory


def extract_requirements(description: str) -> list[Requirement]:
    """Retain the exact source clause; preferred sections never imply a must."""
    result: list[Requirement] = []
    mandatory_section = False
    for raw, evidence, shared_mandatory in _qualification_clauses(description):
        clause = raw.strip("-•* ").rstrip(".")
        if not clause:
            continue
        heading = clause.rstrip(":").casefold()
        if heading in {"requirements", "required qualifications", "minimum qualifications", "essential qualifications", "mandatory qualifications", "required skills"}:
            mandatory_section = True
            continue
        if heading in {"preferred qualifications", "preferred skills", "nice to have", "benefits", "responsibilities", "about us", "qualifications"}:
            mandatory_section = False
            continue
        if re.search(r"^no\b.*\brequired\b", clause, re.I):
            continue
        explicit = bool(re.search(MANDATORY, clause, re.I)) or shared_mandatory
        if re.search(OPTIONAL, clause, re.I):
            # A mixed required/preferred clause is split only at explicit clause
            # boundaries; do not turn the optional item into a requirement.
            if not explicit or re.search(r"\bnot required\b", clause, re.I):
                continue
            optional_part = re.search(r",\s*(?:but\s+)?(.+\b(?:preferred|desirable|optional|a plus)\b.*)$", clause, re.I)
            if optional_part:
                clause = clause[:optional_part.start()]
            elif not re.search(r"\brequired\b.*\b(?:preferred|desirable)\b", clause, re.I):
                continue
        if not explicit and not mandatory_section:
            continue
        start = len(result)
        current = bool(re.search(r"\b(?:valid|active|current)\b", clause, re.I))
        # A credential name is whatever the applicant/employer explicitly names,
        # with aliases for common abbreviations rather than occupation inference.
        for match in re.finditer(r"([A-Za-z][A-Za-z0-9 '/+#-]{0,100}?)\s+(licen[cs]e|registration|certification|certificate)\b", clause, re.I):
            name = _clean_name(match[1])
            kind = "license" if match[2].casefold() in {"license", "licence", "registration"} else "certification"
            if not qualification_key(name):
                name = "Unspecified " + kind
            names = tuple(_clean_name(part) for part in re.split(r"\s+or\s+", name, flags=re.I))
            result.append(Requirement(kind, names[0], evidence, names[1:], current_required=current))
        cdl = re.search(r"\b(?:class\s+[ABC]\s+)?CDL(?:\s+class\s+[ABC])?\b", clause, re.I)
        if cdl and not any(item.kind == "license" and "commercial driver" in qualification_key(item.name) for item in result[start:]):
            cdl_name = re.sub(r"^class\s+([ABC])\s+CDL$", r"CDL Class \1", cdl[0], flags=re.I)
            result.append(Requirement("license", cdl_name, evidence, current_required=current))
        degree = re.search(r"\b(bachelor(?:'?s)?|master(?:'?s)?|associate(?:'?s)?|doctoral|doctorate|BSc|BSN|BS|BA|MSc|MS|MA|PhD)\b(?:\s+degree)?", clause, re.I)
        if degree:
            field = re.search(r"\bdegree\s+in\s+([A-Za-z][A-Za-z &/-]*?)(?=\s+(?:is\s+)?(?:required|mandatory|essential|preferred)\b|[,.;]|$)", clause, re.I)
            result.append(Requirement("degree", degree[0], evidence, field_of_study=field[1].strip() if field else ("Nursing" if degree[1].casefold() == "bsn" else "")))
        languages = list(re.finditer(r"\b(?:" + LANGUAGES + r")\b", clause, re.I))
        proficiency = re.search(r"\b(basic|conversational|professional|fluent|fluency|native)\b", clause, re.I)
        required_proficiency = ("fluent" if proficiency[1].casefold() == "fluency" else proficiency[1].casefold()) if proficiency else ""
        alternatives = len(languages) > 1 and all(re.search(r"\bor\b", clause[left.end():right.start()], re.I) for left, right in zip(languages, languages[1:]))
        if alternatives:
            result.append(Requirement("language", languages[0][0], evidence, tuple(item[0] for item in languages[1:]), proficiency=required_proficiency))
        else:
            result.extend(Requirement("language", language[0], evidence, proficiency=required_proficiency) for language in languages)
        number = r"(?:\d+(?:\.\d+)?|" + "|".join(NUMBER_WORDS) + ")"
        years = re.search(r"\b(" + number + r")(?:\s*(?:[-–]|to)\s*" + number + r")?\+?\s*(?:years?|yrs?)\b", clause, re.I)
        if years:
            tail = clause[years.end():].strip(" '’")
            before = re.search(r"^(?:of\s+)?(.+?)\s+experience\b", tail, re.I)
            after = re.search(r"^(?:of\s+)?experience\s+(?:in|with)\s+(.+?)(?=\s+(?:is\s+)?(?:required|mandatory|essential)\b|$)", tail, re.I)
            name = before[1].strip() if before else after[1].strip() if after else ""
            result.append(Requirement("experience", name, evidence, minimum=float(NUMBER_WORDS[years[1].casefold()] if years[1].casefold() in NUMBER_WORDS else years[1])))
        if len(result) == start:
            skill = re.sub(r"^(?:must\s+(?:have|possess|demonstrate)\s+)?(?:(?:proficiency|knowledge|experience|skills?)\s+(?:in|of|with)\s+)?", "", clause, flags=re.I)
            skill = re.sub(r"\s+(?:is\s+)?(?:required|mandatory|essential)$", "", skill, flags=re.I).strip()
            skill = re.sub(r"\s+skills?$", "", skill, flags=re.I)
            # Leave behavioral/residency/authorization clauses to existing policy.
            if skill and len(skill.split()) <= 6 and not re.search(r"\b(?:must|be|work|live|reside|relocate|authorized|authorised|sponsor|responsible|able)\b", skill, re.I):
                result.extend(Requirement("skill", part.strip(), evidence) for part in re.split(r",|\s+and\s+", skill) if part.strip())
    unique = {}
    for item in result:
        identity = (item.kind, qualification_key(item.name), item.alternatives, item.minimum, item.proficiency, item.field_of_study, item.current_required)
        unique.setdefault(identity, item)
    return list(unique.values())


def _records(profile: Profile) -> ApplicantRecords:
    raw = getattr(profile, "records", None)
    return raw if isinstance(raw, ApplicantRecords) else ApplicantRecords.model_validate(raw or {})


def assess_requirements(description: str, profile: Profile, aliases: dict[str, str] | None = None) -> list[RequirementAssessment]:
    return [assess_requirement(item, profile, aliases) for item in extract_requirements(description)]


def assess_requirement(requirement: Requirement, profile: Profile, aliases: dict[str, str] | None = None) -> RequirementAssessment:
    from .matching import contains, normalized
    records = _records(profile)
    req = requirement
    unknown = RequirementAssessment(req, "unknown", "No confirmed evidence for " + (req.name or "total experience"))
    if req.kind in {"license", "certification"}:
        section = "licenses" if req.kind == "license" else "certifications"
        names = [qualification_key(name) for name in (req.name, *req.alternatives)]
        matches = [record for record in getattr(records, section) if any(qualification_key(record.name) == name for name in names)]
        outcomes = []
        for record in matches:
            source = (f"profile:records:{section}:{record.id}",)
            if record.expiry_date and record.expiry_date < date.today():
                outcomes.append(RequirementAssessment(req, "unmet", f"Confirmed {record.name} expired on {record.expiry_date}", source))
            elif record.issue_date and record.issue_date > date.today():
                outcomes.append(RequirementAssessment(req, "unmet", f"Confirmed {record.name} starts on {record.issue_date}", source))
            elif req.current_required and not record.expiry_date:
                outcomes.append(RequirementAssessment(req, "unknown", f"Current validity of {record.name} is not confirmed", source))
            else:
                outcomes.append(RequirementAssessment(req, "met", "Confirmed " + record.name, source))
        missing_alternative = req.alternatives and any(name not in {qualification_key(item.name) for item in matches} for name in names)
        fallback = unknown if missing_alternative else outcomes[0] if outcomes else unknown
        return next((item for item in outcomes if item.status == "met"), next((item for item in outcomes if item.status == "unknown"), fallback))
    if req.kind == "language":
        matches = [item for item in records.languages if item.name.casefold() in {name.casefold() for name in (req.name, *req.alternatives)}]
        outcomes = []
        for item in matches:
            source = (f"profile:records:languages:{item.id}",)
            if req.proficiency and not item.proficiency:
                outcomes.append(RequirementAssessment(req, "unknown", "Language proficiency not confirmed", source))
            elif not req.proficiency or PROFICIENCY[item.proficiency] >= PROFICIENCY[req.proficiency]:
                outcomes.append(RequirementAssessment(req, "met", f"Confirmed {item.name} proficiency: {item.proficiency or 'not specified'}", source))
            else:
                outcomes.append(RequirementAssessment(req, "unmet", f"Confirmed {item.name} proficiency {item.proficiency} is below {req.proficiency}", source))
        missing_alternative = req.alternatives and any(name.casefold() not in {item.name.casefold() for item in matches} for name in (req.name, *req.alternatives))
        fallback = unknown if missing_alternative else outcomes[0] if outcomes else unknown
        return next((item for item in outcomes if item.status == "met"), next((item for item in outcomes if item.status == "unknown"), fallback))
    if req.kind == "degree":
        level = degree_level(req.name)
        for item in records.education:
            actual = degree_level(item.degree)
            accepts_higher = bool(re.search(r"\b(?:minimum|at least|or higher|or above)\b", req.evidence, re.I))
            if actual is None or level is None or (actual < level if accepts_higher else actual != level):
                continue
            field = item.field_of_study or ("Nursing" if item.degree.casefold() == "bsn" else "")
            if req.field_of_study and not contains(field, req.field_of_study, aliases):
                continue
            if item.current is True or (item.end_date and item.end_date > date.today()):
                continue
            if item.end_date is None and item.current is not False:
                continue
            return RequirementAssessment(req, "met", f"Confirmed {item.degree} in {field}".rstrip(), (f"profile:records:education:{item.id}",))
        return unknown
    if req.kind == "experience":
        if not req.name:
            return unknown
        matches = [(name, years) for name, years in profile.skill_years.items() if normalized(name, aliases) == normalized(req.name, aliases)]
        if matches:
            name, years = max(matches, key=lambda item: item[1])
            status = "met" if years >= req.minimum else "unmet"
            return RequirementAssessment(req, status, f"Confirmed {name}: {years:g} years; requires {req.minimum:g}", ("profile:skill_years:" + name,))
        return unknown
    if any(normalized(skill, aliases) == normalized(req.name, aliases) for skill in profile.skills):
        return RequirementAssessment(req, "met", "Confirmed skill: " + req.name, ("profile:skills",))
    return unknown


def resume_requirement_match(requirement: Requirement, text: str, aliases: dict[str, str] | None = None) -> bool:
    """Term evidence for CV ranking; this does not certify applicant eligibility."""
    from .matching import contains
    req = requirement
    # Negative or explicitly expired statements are not positive qualification
    # evidence even though their words overlap the job description.
    text = " ".join(part for part in re.split(r"(?<=[.!?])\s+|[;\n]+", text) if not re.search(r"\b(?:no|not|without|lack|lacks|lacking|expired)\b", part, re.I))
    if req.kind in {"license", "certification"}:
        content = qualification_key(text)
        return any(contains(content, qualification_key(name)) for name in (req.name, *req.alternatives))
    if req.kind == "degree":
        expected = degree_level(req.name)
        levels = {level for token, level in DEGREE_LEVELS.items() if re.search(r"\b" + token + r"\b", re.sub(r"[^\w]+", " ", text.casefold()))}
        accepts_higher = bool(re.search(r"\b(?:minimum|at least|or higher|or above)\b", req.evidence, re.I))
        level_matches = expected is not None and any(actual >= expected if accepts_higher else actual == expected for actual in levels)
        return level_matches and (not req.field_of_study or contains(text, req.field_of_study, aliases))
    return any(bool(name) and contains(text, name, aliases) for name in (req.name, *req.alternatives))
