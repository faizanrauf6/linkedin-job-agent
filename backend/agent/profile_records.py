"""Confirmed, occupation-neutral applicant facts and conservative form answers."""
from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from .models import Answer


class RecordModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def iso_dates(cls, value: Any, info):
        if info.field_name.endswith("_date") and value is not None:
            if value == "":
                return None
            if isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Use an ISO date: YYYY-MM-DD")
            if not isinstance(value, (str, date)):
                raise ValueError("Use an ISO date: YYYY-MM-DD")
        return value


class IdentifiedRecord(RecordModel):
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)


class DatedRecord(IdentifiedRecord):
    organization: str = ""
    start_date: date | None = None
    end_date: date | None = None
    current: bool | None = None
    description: str = ""

    @model_validator(mode="after")
    def valid_interval(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("End date cannot precede start date")
        if self.current is True and self.end_date:
            raise ValueError("A current record cannot have an end date")
        return self


class EmploymentRecord(DatedRecord):
    title: str = ""


class EducationRecord(DatedRecord):
    degree: str = ""
    field_of_study: str = ""


class CredentialRecord(IdentifiedRecord):
    name: str = ""
    issuer: str = ""
    identifier: str = ""
    issue_date: date | None = None
    expiry_date: date | None = None

    @model_validator(mode="after")
    def valid_interval(self):
        if self.issue_date and self.expiry_date and self.expiry_date < self.issue_date:
            raise ValueError("Expiry date cannot precede issue date")
        return self


class LanguageRecord(IdentifiedRecord):
    name: str = ""
    proficiency: Literal["basic", "conversational", "professional", "fluent", "native"] | None = None


class Address(RecordModel):
    line1: str = ""
    line2: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""


class WorkPreferences(RecordModel):
    willing_to_travel: bool | None = None
    travel_percent: int | None = Field(default=None, ge=0, le=100)
    shifts: list[Literal["day", "evening", "night", "rotating"]] = Field(default_factory=list)
    willing_to_work_weekends: bool | None = None
    willing_to_work_overtime: bool | None = None

    @model_validator(mode="after")
    def valid_travel(self):
        if self.willing_to_travel is False and self.travel_percent:
            raise ValueError("Travel percentage conflicts with unwillingness to travel")
        return self


class SalaryExpectation(RecordModel):
    amount: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    period: Literal["year", "month", "week", "day", "hour"] | None = None

    @model_validator(mode="after")
    def complete_amount(self):
        if self.amount is not None and (not self.currency or not self.period):
            raise ValueError("Salary amount requires currency and period")
        return self


class Availability(RecordModel):
    start_date: date | None = None
    notice_days: int | None = Field(default=None, ge=0, le=3650)
    immediate: bool | None = None

    @model_validator(mode="after")
    def valid_availability(self):
        if self.immediate is True and (self.start_date or self.notice_days):
            raise ValueError("Immediate availability conflicts with a start date or positive notice")
        return self


class ApplicantRecords(RecordModel):
    employment: list[EmploymentRecord] = Field(default_factory=list)
    education: list[EducationRecord] = Field(default_factory=list)
    certifications: list[CredentialRecord] = Field(default_factory=list)
    licenses: list[CredentialRecord] = Field(default_factory=list)
    languages: list[LanguageRecord] = Field(default_factory=list)
    address: Address = Field(default_factory=Address)
    preferences: WorkPreferences = Field(default_factory=WorkPreferences)
    salary: SalaryExpectation = Field(default_factory=SalaryExpectation)
    availability: Availability = Field(default_factory=Availability)

    @model_validator(mode="after")
    def unique_record_ids(self):
        for section in (self.employment, self.education, self.certifications, self.licenses, self.languages):
            if len({record.id for record in section}) != len(section):
                raise ValueError("Record identifiers must be unique within each section")
        return self


def _key(question: str) -> str:
    return re.sub(r"[\W_]+", " ", question.casefold()).strip()


def _text(value: Any) -> str | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return ", ".join(map(str, value))
    if isinstance(value, float):
        return str(value).removesuffix(".0")
    return str(value)


def _answer(value: Any, source: str):
    from .models import Answer
    text = _text(value)
    return Answer(value=text, source=source) if text is not None else None


def _records(profile: Any) -> ApplicantRecords:
    raw = getattr(profile, "records", None)
    return raw if isinstance(raw, ApplicantRecords) else ApplicantRecords.model_validate(raw or {})


_RECORD_LABELS = {
    "employment": {
        "employer": "organization", "employer name": "organization", "company": "organization", "company name": "organization", "organization": "organization",
        "job title": "title", "title": "title", "role": "title", "position": "title",
        "start date": "start_date", "employment start date": "start_date", "end date": "end_date", "employment end date": "end_date",
        "description": "description", "job description": "description", "responsibilities": "description",
        "i currently work here": "current", "current employment": "current",
    },
    "education": {
        "school": "organization", "school name": "organization", "institution": "organization", "institution name": "organization", "university": "organization",
        "degree": "degree", "qualification": "degree", "field of study": "field_of_study", "major": "field_of_study",
        "start date": "start_date", "education start date": "start_date", "end date": "end_date", "graduation date": "end_date",
        "description": "description", "i currently study here": "current", "currently enrolled": "current",
    },
    "licenses": {"license name": "name", "licence name": "name", "license number": "identifier", "licence number": "identifier", "license identifier": "identifier", "license issuer": "issuer", "licensing authority": "issuer", "license expiry date": "expiry_date", "license expiration date": "expiry_date", "license issue date": "issue_date"},
    "certifications": {"certification name": "name", "certification number": "identifier", "certification identifier": "identifier", "certification issuer": "issuer", "certification expiry date": "expiry_date", "certification expiration date": "expiry_date", "certification issue date": "issue_date"},
    "languages": {"language": "name", "language name": "name", "language proficiency": "proficiency", "proficiency": "proficiency"},
}


def record_answer(section: str, record: dict | BaseModel, question: str) -> Answer | None:
    """Resolve an exact field label in an explicitly selected repeating record."""
    section = {"work": "employment", "work_history": "employment", "license": "licenses", "certification": "certifications", "language": "languages"}.get(section, section)
    models = {"employment": EmploymentRecord, "education": EducationRecord, "licenses": CredentialRecord, "certifications": CredentialRecord, "languages": LanguageRecord}
    if section not in models:
        return None
    # Validate dicts too: callers must never bypass contradictory-date checks.
    item = models[section].model_validate(record.model_dump() if isinstance(record, BaseModel) else record)
    key = _key(question)
    if section == "education" and key == "graduation year":
        return _answer(item.end_date.year if item.end_date else None, f"profile:records:{section}:{item.id}:end_date")
    field = _RECORD_LABELS[section].get(key)
    return _answer(getattr(item, field), f"profile:records:{section}:{item.id}:{field}") if field else None


def structured_answer(question: str, profile: Any) -> Answer | None:
    """Use only exact semantic labels; ambiguous collections need record context."""
    records = _records(profile)
    key = _key(question)
    sections = [section for section, labels in _RECORD_LABELS.items() if key in labels or (section == "education" and key == "graduation year")]
    populated = [section for section in sections if getattr(records, section)]
    if len(populated) == 1:
        section = populated[0]
        items = getattr(records, section)
        if len(items) == 1:
            return record_answer(section, items[0], question)
    if key in {"current employer", "current job title"}:
        current = [item for item in records.employment if item.current is True]
        if len(current) == 1:
            return record_answer("employment", current[0], "employer" if key == "current employer" else "job title")
    fields = {
        "street address": ("address", "line1"), "address line 1": ("address", "line1"), "address line 2": ("address", "line2"),
        "city": ("address", "city"), "state": ("address", "region"), "province": ("address", "region"), "region": ("address", "region"),
        "postal code": ("address", "postal_code"), "zip code": ("address", "postal_code"), "country": ("address", "country"),
        "are you willing to travel": ("preferences", "willing_to_travel"), "travel percentage": ("preferences", "travel_percent"), "preferred shifts": ("preferences", "shifts"),
        "are you willing to work weekends": ("preferences", "willing_to_work_weekends"), "are you willing to work overtime": ("preferences", "willing_to_work_overtime"),
        "salary amount": ("salary", "amount"), "salary currency": ("salary", "currency"), "salary period": ("salary", "period"),
        "available start date": ("availability", "start_date"), "earliest start date": ("availability", "start_date"),
        "notice period in days": ("availability", "notice_days"), "are you available immediately": ("availability", "immediate"),
    }
    if key == "salary expectation" and records.salary.amount is not None:
        salary = records.salary
        return _answer(f"{salary.currency} {_text(salary.amount)} per {salary.period}", "profile:records:salary")
    if key in fields:
        section, field = fields[key]
        return _answer(getattr(getattr(records, section), field), f"profile:records:{section}:{field}")
    return None


def profile_facts(profile: Any) -> list[tuple[str, str]]:
    """Return independently attributed facts for evidence-based matching and docs."""
    facts = []
    for section, values in _records(profile).model_dump(mode="json").items():
        if isinstance(values, list):
            for item in values:
                for field, value in item.items():
                    text = _text(value)
                    if field != "id" and text is not None:
                        facts.append((f"profile:records:{section}:{item['id']}:{field}", text))
        else:
            for field, value in values.items():
                text = _text(value)
                if text is not None:
                    facts.append((f"profile:records:{section}:{field}", text))
    return facts
