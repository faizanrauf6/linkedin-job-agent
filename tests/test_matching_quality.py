"""Matching decisions must distinguish explicit evidence from unknown facts."""
from datetime import date, timedelta

import pytest

from agent.matching import match_job, select_resume
from agent.models import Job, Profile, Resume, Search


def vacancy(title="Registered Nurse", description="", **kwargs):
    return Job(linkedin_id="quality", title=title, description=description, company="Care Co", **kwargs)


def applicant(**records):
    return Profile(records=records)


def test_missing_mandatory_nursing_license_needs_review_without_asserting_absence():
    result = match_job(vacancy(description="Must hold a Registered Nurse license."), Search(min_score=0), Profile())
    assert not result.eligible
    assert result.needs_review
    assert any("unknown" in reason.lower() and "license" in reason.lower() for reason in result.reasons)
    assert not any("unmet" in reason.lower() for reason in result.reasons)


def test_unknown_mandatory_requirement_respects_reject_policy():
    result = match_job(vacancy(description="Must hold an RN license."), Search(min_score=0, missing_information="reject"), Profile())
    assert not result.eligible
    assert not result.needs_review


def test_expired_confirmed_license_is_unmet_and_rejected():
    p = applicant(licenses=[{"name":"Registered Nurse license", "expiry_date":(date.today()-timedelta(days=1)).isoformat()}])
    result = match_job(vacancy(description="A valid RN license is required."), Search(min_score=0), p)
    assert not result.eligible
    assert not result.needs_review
    assert any("unmet" in reason.lower() and "expired" in reason.lower() for reason in result.reasons)


def test_required_cdl_license_matches_long_form_confirmed_license():
    p = applicant(licenses=[{"name":"Commercial Driver License Class A", "expiry_date":"2099-12-31"}])
    result = match_job(vacancy("CDL Driver", "Must hold a valid CDL Class A license."), Search(titles=["Truck Driver"], min_score=80), p)
    assert result.eligible
    assert any("met" in reason.lower() and "license" in reason.lower() for reason in result.reasons)


def test_cdl_class_is_never_inferred_from_generic_license():
    p = applicant(licenses=[{"name":"Commercial Driver License", "expiry_date":"2099-12-31"}])
    result = match_job(vacancy("Truck Driver", "CDL Class A required."), Search(min_score=0), p)
    assert not result.eligible and result.needs_review


def test_preferred_license_degree_and_years_do_not_become_mandatory():
    text = "Registered Nurse license preferred. A bachelor's degree is a plus. Five years of experience preferred."
    result = match_job(vacancy(description=text), Search(min_score=0), Profile())
    assert result.eligible


def test_required_and_preferred_clauses_in_same_sentence_keep_requirement():
    text = "RN license required; Bachelor degree preferred."
    result = match_job(vacancy(description=text), Search(min_score=0), Profile())
    assert not result.eligible and result.needs_review
    assert not any("degree" in reason.lower() for reason in result.reasons)


def test_explicit_junior_target_rejects_senior_title_even_without_metadata():
    result = match_job(vacancy("Senior Software Engineer", "Python"), Search(titles=["Junior Software Engineer"], min_score=0), Profile())
    assert not result.eligible
    assert any("seniority" in reason.lower() for reason in result.reasons)


def test_title_supplies_missing_seniority_metadata():
    result = match_job(vacancy("Junior Software Developer", "Python"), Search(seniorities=["entry level"], min_score=0), Profile())
    assert result.eligible


def test_aliases_match_rn_title_without_confusing_nurse_practitioner():
    search = Search(titles=["Registered Nurse"], min_score=90)
    assert match_job(vacancy("RN"), search, Profile()).eligible
    assert not match_job(vacancy("Nurse Practitioner"), search, Profile()).eligible


def test_specific_confirmed_years_can_be_unmet_without_guessing_general_tenure():
    p = Profile(skill_years={"Python":2})
    result = match_job(vacancy("Software Developer", "Minimum 5 years of Python experience required."), Search(min_score=0), p)
    assert not result.eligible and not result.needs_review
    assert any("unmet" in reason.lower() and "2" in reason for reason in result.reasons)
    unknown = match_job(vacancy("Software Developer", "Minimum 5 years of experience required."), Search(min_score=0), p)
    assert not unknown.eligible and unknown.needs_review


def test_language_requirement_uses_confirmed_proficiency():
    text = "Fluent Spanish required."
    low = match_job(vacancy(description=text), Search(min_score=0), applicant(languages=[{"name":"Spanish","proficiency":"basic"}]))
    assert not low.eligible and not low.needs_review
    met = match_job(vacancy(description=text), Search(min_score=0), applicant(languages=[{"name":"Spanish","proficiency":"native"}]))
    assert met.eligible


def test_degree_requirement_is_unknown_when_education_is_not_confirmed():
    result = match_job(vacancy(description="Bachelor's degree in Nursing required."), Search(min_score=0), Profile())
    assert not result.eligible and result.needs_review
    met = match_job(vacancy(description="Bachelor's degree in Nursing required."), Search(min_score=0), applicant(education=[{"degree":"BSc","field_of_study":"Nursing","end_date":"2020-06-30"}]))
    assert met.eligible


def test_requirement_assessment_exposes_original_evidence_and_sources():
    from agent.requirements import assess_requirements
    assessments = assess_requirements("Must hold a valid CDL Class A license.", applicant(licenses=[{"id":"cdl","name":"Commercial Driver License Class A", "expiry_date":"2099-12-31"}]))
    assert len(assessments) == 1
    assert assessments[0].status == "met"
    assert assessments[0].requirement.evidence == "Must hold a valid CDL Class A license."
    assert any("profile:records:licenses:cdl" in source for source in assessments[0].sources)


def test_requirement_headings_keep_preferred_bullets_optional():
    from agent.requirements import extract_requirements
    requirements = extract_requirements("Minimum Qualifications:\n- RN license\nPreferred Qualifications:\n- Master's degree\n- BLS certification")
    assert len(requirements) == 1
    assert requirements[0].kind == "license"
    assert "RN license" in requirements[0].evidence


def test_alternative_licenses_are_not_treated_as_both_required():
    text = "RN or LPN license required."
    p = applicant(licenses=[{"name":"Licensed Practical Nurse license"}])
    result = match_job(vacancy(description=text), Search(min_score=0), p)
    assert result.eligible


def test_role_fit_outweighs_irrelevant_cv_priority_and_generic_word_overlap():
    resumes = [
        Resume(id="nurse",label="Nursing",roles=["Registered Nurse"],skills=["patient care"],text="RN. Ward care and clinical documentation.",priority=0),
        Resume(id="sales",label="Sales",roles=["Sales Representative"],text="We sell patient care software and clinical documentation solutions to registered nurses.",priority=100),
    ]
    choice = select_resume(vacancy("Registered Nurse", "Patient care and clinical documentation"), resumes)
    assert choice is not None and choice.resume.id == "nurse"
    assert any("role" in reason.lower() for reason in choice.reasons)


def test_required_license_evidence_breaks_role_tie_before_priority():
    resumes = [
        Resume(id="general",label="General Driver",roles=["Truck Driver"],text="Truck Driver. Logistics and deliveries.",priority=100),
        Resume(id="cdl",label="CDL",roles=["Truck Driver"],text="Truck Driver. CDL Class A license. Logistics and deliveries.",priority=0),
    ]
    choice = select_resume(vacancy("Truck Driver", "CDL Class A required. Logistics and deliveries."), resumes)
    assert choice is not None and choice.resume.id == "cdl"
    assert any("mandatory" in reason.lower() for reason in choice.reasons)


def test_resume_selection_accepts_optional_profile_without_changing_old_call_contract():
    p = applicant(licenses=[{"name":"Commercial Driver License Class A"}])
    resume = Resume(label="Driver",roles=["Truck Driver"],text="Truck Driver. CDL Class A license.")
    choice = select_resume(vacancy("CDL Driver", "CDL Class A required."), [resume], 20, None, p)
    assert choice is not None
    assert any("profile" in reason.lower() for reason in choice.reasons)


def test_cv_priority_only_breaks_equal_relevance_ties():
    resumes = [Resume(id="a",label="A",text="Python engineer",priority=2), Resume(id="b",label="B",text="Python engineer",priority=3)]
    choice = select_resume(vacancy("Python engineer", "Python"),resumes)
    assert choice.resume.id == "b"
    assert any("priority" in reason.lower() for reason in choice.reasons)


@pytest.mark.parametrize("text", [
    "RN license required and BLS certification preferred.",
    "BLS certification preferred but RN license required.",
    "BLS certification preferred, RN license required.",
])
def test_mixed_preferred_and_required_qualifications_assess_only_mandatory_item(text):
    result = match_job(vacancy(description=text), Search(min_score=0), applicant(licenses=[{"name":"Registered Nurse"}]))
    assert result.eligible
    missing = match_job(vacancy(description=text), Search(min_score=0), Profile())
    assert not missing.eligible and missing.needs_review


def test_multiple_mandatory_credentials_keep_separate_names():
    p = applicant(licenses=[{"name":"Registered Nurse"}],certifications=[{"name":"Basic Life Support"}])
    result = match_job(vacancy(description="Must hold RN license and BLS certification."),Search(min_score=0),p)
    assert result.eligible


def test_negative_license_requirement_is_not_mandatory():
    result=match_job(vacancy(description="No RN license required."),Search(min_score=0),Profile())
    assert result.eligible


def test_unspecified_mandatory_license_requires_clarification():
    result=match_job(vacancy(description="Must hold a valid license to practice."),Search(min_score=0),Profile())
    assert not result.eligible and result.needs_review


def test_cdl_class_prefix_cannot_match_unspecified_cdl():
    result=match_job(vacancy("Driver","Valid Class A CDL required."),Search(min_score=0),applicant(licenses=[{"name":"CDL","expiry_date":"2099-01-01"}]))
    assert not result.eligible and result.needs_review


def test_alternative_languages_require_only_one_confirmed_language():
    result=match_job(vacancy(description="Spanish or French required."),Search(min_score=0),applicant(languages=[{"name":"French","proficiency":"fluent"}]))
    assert result.eligible


def test_fluency_is_not_satisfied_by_basic_language_knowledge():
    result=match_job(vacancy(description="Fluency in Spanish required."),Search(min_score=0),applicant(languages=[{"name":"Spanish","proficiency":"basic"}]))
    assert not result.eligible and not result.needs_review


def test_experience_range_uses_lower_bound_as_minimum():
    result=match_job(vacancy("Developer","3-5 years of Python experience required."),Search(min_score=0),Profile(skill_years={"Python":3}))
    assert result.eligible


def test_skill_list_requires_each_named_skill_without_attached_label_words():
    result=match_job(vacancy("Developer","Must have Python and SQL skills."),Search(min_score=0),Profile(skills=["Python","SQL"]))
    assert result.eligible


def test_incidental_seniority_word_in_cv_prose_does_not_override_role_fit():
    cv=Resume(label="Nurse",text="Senior RN\nMentored ward staff during patient care.")
    result=select_resume(vacancy("Senior Registered Nurse","Patient care"),[cv],minimum=80)
    assert result is not None


def test_written_number_years_are_checked_against_confirmed_experience():
    result=match_job(vacancy("Nurse","At least five years of nursing experience required."),Search(min_score=0),Profile(skill_years={"nursing":2}))
    assert not result.eligible and not result.needs_review


def test_either_license_alternative_keeps_first_name_intact():
    result=match_job(vacancy(description="Must hold either RN or LPN license."),Search(min_score=0),applicant(licenses=[{"name":"Registered Nurse"}]))
    assert result.eligible


def test_higher_degree_does_not_invent_the_exact_required_degree():
    p=applicant(education=[{"degree":"MSc","field_of_study":"Nursing","end_date":"2020-06-30"}])
    exact=match_job(vacancy(description="BSN required."),Search(min_score=0),p)
    assert not exact.eligible and exact.needs_review
    minimum=match_job(vacancy(description="At least a bachelor's degree in Nursing required."),Search(min_score=0),p)
    assert minimum.eligible


@pytest.mark.parametrize("text,records", [
    ("RN or LPN license required.", {"licenses":[{"name":"Registered Nurse","expiry_date":"2020-01-01"}]}),
    ("Fluent Spanish or French required.", {"languages":[{"name":"Spanish","proficiency":"basic"}]}),
])
def test_unconfirmed_alternative_prevents_definite_rejection(text,records):
    result=match_job(vacancy(description=text),Search(min_score=0),applicant(**records))
    assert not result.eligible and result.needs_review


def test_cv_degree_check_can_find_required_degree_among_multiple_degrees():
    cv=Resume(label="Nurse",roles=["Registered Nurse"],text="Registered Nurse. BSc Nursing. MSc Nursing.")
    result=select_resume(vacancy(description="Master's degree in Nursing required."),[cv],minimum=80)
    assert result is not None
    assert any("Mandatory qualification terms: 1/1" in reason for reason in result.reasons)


def test_denied_license_is_not_positive_mandatory_cv_evidence():
    resumes=[
        Resume(id="denied",label="No CDL",roles=["Truck Driver"],text="Truck Driver. No CDL Class A license. Logistics.",priority=100),
        Resume(id="valid",label="CDL",roles=["Truck Driver"],text="Truck Driver. CDL Class A license. Logistics.",priority=0),
    ]
    result=select_resume(vacancy("Truck Driver","CDL Class A license required. Logistics."),resumes)
    assert result.resume.id=="valid"
