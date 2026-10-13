"""Structured facts must round-trip without guessing or conflating records."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent.profile_records import ApplicantRecords, profile_facts, record_answer, structured_answer


def profile(**records):
    return SimpleNamespace(records=ApplicantRecords.model_validate(records))


def test_records_roundtrip_preserves_identifiers_and_occupation_neutral_facts():
    records = ApplicantRecords(
        employment=[{"organization": "County Hospital", "title": "Registered Nurse", "start_date": "2020-03-01", "current": True, "description": "Provided ward care."}],
        education=[{"organization": "Community College", "degree": "Diploma", "field_of_study": "Nursing", "start_date": "2017-09-01", "end_date": "2019-06-30"}],
        licenses=[{"name": "Registered Nurse", "issuer": "Nursing Board", "identifier": "RN-42", "expiry_date": "2030-06-30"}],
        certifications=[{"name": "Basic Life Support", "issuer": "Training Centre"}],
        languages=[{"name": "Urdu", "proficiency": "native"}],
    )
    encoded = records.model_dump_json()
    restored = ApplicantRecords.model_validate_json(encoded)
    assert restored.employment[0].id == records.employment[0].id
    assert restored.employment[0].title == "Registered Nurse"
    assert restored.education[0].end_date.isoformat() == "2019-06-30"
    assert restored.licenses[0].identifier == "RN-42"
    assert restored.languages[0].proficiency == "native"


def test_missing_legacy_records_produce_no_new_facts_or_answers():
    assert ApplicantRecords().employment == []
    assert profile_facts(SimpleNamespace(work_history="Legacy text")) == []
    assert structured_answer("License number", SimpleNamespace()) is None
    assert structured_answer("Salary expectation", profile()) is None


@pytest.mark.parametrize("data", [
    {"employment": [{"start_date": "2023-02-30"}]},
    {"employment": [{"start_date": "2024-01-01", "end_date": "2023-01-01"}]},
    {"education": [{"current": True, "end_date": "2025-01-01"}]},
    {"employment": [{"start_date": "1700000000"}]},
    {"licenses": [{"issue_date": "2025-01-01", "expiry_date": "2024-12-31"}]},
    {"employment": [{"id": "same"}, {"id": "same"}]},
    {"salary": {"amount": -1, "currency": "USD", "period": "year"}},
    {"salary": {"amount": 100}},
    {"salary": {"amount": 100, "currency": "dollars", "period": "year"}},
    {"salary": {"amount": float("inf"), "currency": "USD", "period": "year"}},
    {"availability": {"notice_days": -1}},
    {"availability": {"immediate": True, "notice_days": 10}},
    {"availability": {"immediate": True, "start_date": "2030-01-01"}},
    {"preferences": {"travel_percent": 101}},
    {"preferences": {"willing_to_travel": False, "travel_percent": 10}},
    {"languages": [{"name": "English", "proficiency": "invented"}]},
])
def test_invalid_or_contradictory_records_are_rejected(data):
    with pytest.raises(ValidationError):
        ApplicantRecords.model_validate(data)


def test_truck_driver_license_and_salary_answer_only_known_exact_fields():
    p = profile(
        licenses=[{"name": "Commercial Driver License", "identifier": "CDL-123", "issuer": "State DMV", "expiry_date": "2030-04-01"}],
        salary={"amount": 25.5, "currency": "USD", "period": "hour"},
        availability={"notice_days": 14},
        preferences={"willing_to_travel": True, "travel_percent": 30, "shifts": ["night"]},
    )
    assert structured_answer("License number", p).value == "CDL-123"
    assert structured_answer("License expiry date", p).value == "2030-04-01"
    assert structured_answer("Salary expectation", p).value == "USD 25.5 per hour"
    assert structured_answer("Notice period in days", p).value == "14"
    assert structured_answer("Are you willing to travel?", p).value == "Yes"
    assert structured_answer("Are you willing to travel internationally every week?", p) is None
    assert structured_answer("Do you hold a valid nursing license?", p) is None
    assert structured_answer("Are you available for any shift?", p) is None
    assert structured_answer("Annual salary expectation", p) is None


def test_repeating_records_require_context_and_never_pick_first():
    p = profile(employment=[{"organization": "Hospital A", "title": "Nurse"}, {"organization": "Hospital B", "title": "Charge Nurse"}],
                licenses=[{"identifier": "RN-1"}, {"identifier": "CDL-2"}])
    assert structured_answer("Employer", p) is None
    assert structured_answer("Job title", p) is None
    assert structured_answer("License number", p) is None
    assert record_answer("employment", p.records.employment[1], "Employer").value == "Hospital B"
    assert record_answer("employment", p.records.employment[1], "Job title").value == "Charge Nurse"
    assert record_answer("employment", p.records.employment[1], "Start date") is None
    assert record_answer("employment", p.records.employment[1], "Why did you leave?") is None


def test_record_answers_preserve_dates_degrees_and_current_flag():
    work = {"organization": "Software Cooperative", "title": "Developer", "start_date": "2022-01-02", "current": True}
    assert record_answer("employment", work, "Start date").value == "2022-01-02"
    assert record_answer("employment", work, "I currently work here").value == "Yes"
    assert record_answer("employment", work, "End date") is None
    assert record_answer("education", {"degree": "BSc", "end_date": "2021-06-01"}, "Degree").value == "BSc"
    assert record_answer("education", {"degree": "BSc", "end_date": "2021-06-01"}, "Graduation year").value == "2021"
    assert record_answer("education", {"degree": "BSc"}, "GPA") is None


def test_address_preferences_and_availability_do_not_assume_absent_values():
    p = profile(address={"line1": "10 Main Street", "city": "Lahore", "postal_code": "54000"}, availability={"start_date": "2030-01-02"}, preferences={"willing_to_work_weekends": False})
    assert structured_answer("Street address", p).value == "10 Main Street"
    assert structured_answer("Postal code", p).value == "54000"
    assert structured_answer("Available start date", p).value == "2030-01-02"
    assert structured_answer("Are you willing to work weekends?", p).value == "No"
    assert structured_answer("Are you willing to work overtime?", p) is None
    assert structured_answer("Notice period in days", p) is None


def test_grounded_facts_keep_records_separate_and_omit_unknown_values():
    facts = dict(profile_facts(profile(employment=[{"id": "nurse", "title": "Registered Nurse", "organization": "Hospital", "start_date": "2020-01-02", "current": True}], licenses=[{"id": "rn", "name": "Nursing license", "identifier": "RN-42"}])))
    assert facts["profile:records:employment:nurse:title"] == "Registered Nurse"
    assert facts["profile:records:employment:nurse:start_date"] == "2020-01-02"
    assert facts["profile:records:licenses:rn:identifier"] == "RN-42"
    assert not any("expiry_date" in key for key in facts)
    assert not any("salary" in key for key in facts)


def test_dates_and_generic_descriptions_need_section_context():
    p = profile(employment=[{"start_date": "2022-01-01", "description": "Ward care"}], education=[{"start_date": "2018-01-01", "description": "Nursing diploma"}])
    assert structured_answer("Start date", p) is None
    assert structured_answer("Description", p) is None
    assert structured_answer("Employment start date", p).value == "2022-01-01"
    assert structured_answer("Education start date", p).value == "2018-01-01"


def test_salary_answers_preserve_user_amount_without_rounding():
    p = profile(salary={"amount": 1234567.89, "currency": "PKR", "period": "year"})
    assert structured_answer("Salary amount", p).value == "1234567.89"
    assert structured_answer("Salary expectation", p).value == "PKR 1234567.89 per year"
    assert dict(profile_facts(p))["profile:records:salary:amount"] == "1234567.89"
