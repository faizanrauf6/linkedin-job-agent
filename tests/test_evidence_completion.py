import io
import json

import httpx
import pytest
from docx import Document

from agent import resumes
from agent.answers import resolve_answer
from agent.matching import match_job, select_resume
from agent.model_gateway import narrative_answer
from agent.models import Job, Profile, Resume, Search, Settings


def role(**kwargs):
    return Job(**({"linkedin_id": "evidence", "title": "Frontend Engineer", "company": "Acme", "description": "React web applications", "country": "Canada"} | kwargs))


def test_import_extracts_only_explicit_identity_facts(tmp_path):
    document = Document()
    for line in ["Name: Alex Example", "Email: alex@example.com", "Phone: +1 (604) 555-0199", "https://www.linkedin.com/in/alex-example/", "Built accessible React applications."]:
        document.add_paragraph(line)
    stream = io.BytesIO()
    document.save(stream)
    resume = resumes.import_resume(stream.getvalue(), "resume.docx", "My CV", tmp_path)
    assert resume.identity_facts["full_name"] == "Alex Example"
    assert resume.identity_facts["email"] == "alex@example.com"
    assert resume.identity_facts["phone"] == "+1 (604) 555-0199"
    assert resume.identity_facts["linkedin_url"].endswith("/in/alex-example/")
    assert not resume.confirmed


def test_ambiguous_header_is_not_a_name_or_missing_fact_conflict():
    resume = Resume(label="CV", text="Senior Frontend Engineer\nBuilt React applications")
    assert resumes.assess_resume(resume, Profile(first_name="Alex", last_name="Example", email="alex@example.com"), []) == []
    assert "full_name" not in resume.identity_facts


def test_identity_conflicts_compare_profile_and_peers_without_exposing_values():
    resume = Resume(id="candidate", label="CV", text="Name: Alex Example\nEmail: alex@example.com\nPhone: +1 604 555 0199")
    peer = Resume(id="peer", label="Other CV", text="Name: Other Person\nEmail: other@example.com")
    conflicts = resumes.assess_resume(resume, Profile(first_name="Alex", last_name="Example", email="wrong@example.com", phone="+1 (604) 555-0199"), [peer])
    assert any("email" in c and "profile" in c for c in conflicts)
    assert any("full_name" in c and "peer" in c for c in conflicts)
    assert not any("phone" in c for c in conflicts)
    assert all("@" not in c and "Other Person" not in c for c in conflicts)
    assert resume.conflicts == conflicts
    resume.conflict_acknowledged = True
    resumes.assess_resume(resume, Profile(email="wrong@example.com"), [peer])
    assert resume.conflict_acknowledged
    resumes.assess_resume(resume, Profile(email="alex@example.com"), [peer])
    assert not resume.conflict_acknowledged


def test_identity_profile_urls_normalize_without_hiding_different_ids():
    resume = Resume(label="CV", text="LinkedIn: https://www.linkedin.com/in/alex/?trk=cv")
    assert resumes.assess_resume(resume, Profile(linkedin_url="https://linkedin.com/in/alex"), []) == []
    assert resumes.assess_resume(resume, Profile(linkedin_url="https://linkedin.com/in/other"), [])


@pytest.mark.parametrize("question, expected", [("School name", "Example University"), ("Degree", "Bachelor of Science"), ("Field of study", "Computer Science"), ("Graduation year", "2021")])
def test_education_answers_use_explicit_structured_labels(question, expected):
    p = Profile(education="Institution: Example University\nDegree: Bachelor of Science\nField of study: Computer Science\nGraduation year: 2021")
    answer = resolve_answer(question, p)
    assert answer.value == expected
    assert answer.source.startswith("profile:education")


def test_education_does_not_infer_unlabeled_or_ambiguous_values():
    assert resolve_answer("Degree", Profile(education="Studied computing at Example University in 2021")) is None
    assert resolve_answer("Degree", Profile(education="Degree: BA\nDegree: MSc")) is None


def test_relocation_answers_need_explicit_target_preference():
    profile = Profile(willing_to_relocate=True, relocation_countries=["Canada"])
    assert resolve_answer("Are you willing to relocate?", profile).value == "Yes"
    assert resolve_answer("Are you willing to relocate to Canada?", profile).value == "Yes"
    assert resolve_answer("Are you willing to relocate to Germany?", profile) is None
    assert resolve_answer("Are you unwilling to relocate?", profile) is None
    assert resolve_answer("Are you willing to relocate?", Profile()) is None
    assert resolve_answer("Are you willing to relocate?", Profile(willing_to_relocate=False)).value == "No"


def test_residency_restriction_only_allows_explicit_supported_relocation():
    profile = Profile(country="Pakistan", willing_to_relocate=True, relocation_countries=["Canada"])
    search = Search(min_score=0)
    text = "Must be based in Canada. React applications."
    assert not match_job(role(description=text), search, profile).eligible
    assert match_job(role(description=text + " Relocation assistance is provided."), search, profile).eligible
    assert not match_job(role(description=text + " No relocation assistance is available."), search, profile).eligible
    assert not match_job(role(description=text + " Relocation assistance is provided."), search, Profile(country="Pakistan", willing_to_relocate=True)).eligible
    assert not match_job(role(description="Must be currently based in Canada. Relocation assistance is provided."), search, profile).eligible


def test_custom_technology_aliases_are_configuration_local():
    search = Search(required_skills=["react"], min_score=0)
    job = role(description="AcmeUI web applications")
    assert match_job(job, search, Profile(), aliases={"acmeui": "react"}).eligible
    assert not match_job(job, search, Profile()).eligible
    cvs = [Resume(id="a", label="React", text="React web applications"), Resume(id="z", label="Python", text="Python web applications")]
    assert select_resume(job, cvs, minimum=0, aliases={"acmeui": "react"}).resume.id == "a"


def model_transport(monkeypatch, callback):
    original = httpx.AsyncClient
    monkeypatch.setattr("agent.model_gateway.keyring.get_password", lambda *a: None)
    monkeypatch.setattr("agent.model_gateway.httpx.AsyncClient", lambda **kw: original(transport=httpx.MockTransport(callback), **kw))


@pytest.mark.asyncio
async def test_narrative_uses_confirmed_cv_with_redaction_and_retained_source(monkeypatch):
    cv = Resume(id="selected", label="CV", confirmed=True, text="Name: Alex Example\nEmail: alex@example.com\nPhone: +1 604 555 0199\nDate of birth: 1991-01-02\nBuilt accessible React applications.")
    def provider(request):
        body = json.loads(request.content)
        raw = request.content.decode()
        assert "alex@example.com" not in raw and "604 555" not in raw and "1991-01-02" not in raw and "Alex Example" not in raw
        payload = json.loads(body["messages"][1]["content"])
        fact_ids = [key for key, value in payload["confirmed_facts"].items() if value == "Built accessible React applications."]
        assert fact_ids and fact_ids[0].startswith("resume:selected:")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"fact_ids": fact_ids})}}]})
    model_transport(monkeypatch, provider)
    answer = await narrative_answer("Why are you interested in this role?", role(), Profile(), cv, Settings(model_enabled=True, model_name="test"))
    assert answer and "Frontend Engineer" in answer.value
    assert "Built accessible React applications." in answer.value
    assert "resume:selected:" in answer.source


@pytest.mark.asyncio
async def test_narrative_never_uses_unconfirmed_or_conflicted_cv(monkeypatch):
    def forbidden(request):
        raise AssertionError("Unconfirmed CV must never reach provider")
    model_transport(monkeypatch, forbidden)
    settings = Settings(model_enabled=True, model_name="test")
    for cv in [Resume(label="CV", text="Built React applications"), Resume(label="CV", confirmed=True, conflicts=["email differs"], text="Built React applications")]:
        assert await narrative_answer("Tell us about your experience", role(), Profile(), cv, settings) is None


@pytest.mark.asyncio
async def test_narrative_rejects_model_authored_claims_and_redacts_profile_prose(monkeypatch):
    def provider(request):
        assert "secret@example.com" not in request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"fact_ids": ["0"], "answer": "I have 20 years of React experience"})}}]})
    model_transport(monkeypatch, provider)
    assert await narrative_answer("Tell us about your experience", role(), Profile(summary="Built React applications. Contact secret@example.com"), Resume(label="CV"), Settings(model_enabled=True, model_name="test")) is None


def test_alias_targets_can_use_builtin_spellings_without_cascading_cycles():
    search = Search(required_skills=["react"], min_score=0)
    assert match_job(role(description="AcmeUI"), search, Profile(), aliases={"acmeui": "react.js"}).eligible
    assert match_job(role(description="Alpha"), Search(required_skills=["beta"], min_score=0), Profile(), aliases={"alpha": "beta", "beta": "alpha"}).eligible


def test_current_residence_wording_cannot_be_bypassed_by_relocation():
    profile = Profile(country="Pakistan", willing_to_relocate=True, relocation_countries=["Canada"])
    for wording in ["Must currently be based in Canada.", "Applicants must currently reside in Canada."]:
        assert not match_job(role(description=wording + " Relocation assistance is available."), Search(min_score=0), profile).eligible


def test_common_contact_line_extracts_contacts_without_interpreting_employer_references():
    cv = Resume(label="CV", text="alex@example.com | +1 (604) 555-0199 | https://linkedin.com/in/alex\nWorked with recruiter other@example.com on hiring.")
    resumes.assess_resume(cv, Profile(), [])
    assert cv.identity_facts["email"] == "alex@example.com"
    assert cv.identity_facts["phone"] == "+1 (604) 555-0199"
    assert cv.identity_facts["linkedin_url"] == "https://linkedin.com/in/alex"


@pytest.mark.asyncio
async def test_narrative_excludes_ambiguous_personal_headers_and_sensitive_inline_details(monkeypatch):
    cv = Resume(id="selected", label="CV", confirmed=True, text="Alex Middle Example\nSocial Security Number 123-45-6789\nDate of birth 1991-01-02\nBuilt accessible React applications.")
    def provider(request):
        raw = request.content.decode()
        assert "Alex Middle Example" not in raw and "123-45-6789" not in raw and "1991-01-02" not in raw
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        ids = [key for key, value in payload["confirmed_facts"].items() if value == "Built accessible React applications."]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"fact_ids": ids})}}]})
    model_transport(monkeypatch, provider)
    assert await narrative_answer("Tell us about your experience", role(), Profile(), cv, Settings(model_enabled=True, model_name="test"))


@pytest.mark.asyncio
async def test_profile_work_history_preserves_first_fact_id(monkeypatch):
    def provider(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"fact_ids": ["0"]}'}}]})
    model_transport(monkeypatch, provider)
    answer = await narrative_answer("Tell us about your experience", role(), Profile(work_history="I built React applications."), Resume(label="CV"), Settings(model_enabled=True, model_name="test"))
    assert answer and answer.value == "I built React applications."


def test_malformed_contact_urls_do_not_break_cv_assessment():
    cv = Resume(label="CV", text="Name: Alex Example\nWebsite: https://[broken\nBuilt React applications.")
    assert resumes.assess_resume(cv, Profile(linkedin_url="https://[broken"), []) == []


@pytest.mark.asyncio
async def test_narrative_removes_unpunctuated_sensitive_demographics(monkeypatch):
    cv = Resume(label="CV", confirmed=True, text="Nationality Pakistani frontend engineer\nGender female software developer\nBuilt React applications.")
    def provider(request):
        raw = request.content.decode()
        assert "Pakistani" not in raw and "female" not in raw
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        ids = [key for key, value in payload["confirmed_facts"].items() if value == "Built React applications."]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"fact_ids": ids})}}]})
    model_transport(monkeypatch, provider)
    assert await narrative_answer("Tell us about your experience", role(), Profile(), cv, Settings(model_enabled=True, model_name="test"))
