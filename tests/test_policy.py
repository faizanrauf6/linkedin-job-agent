import pytest

from agent.models import Job, Profile, Resume, Search
from agent.matching import match_job, select_resume
from agent.answers import resolve_answer
from agent.security import public_url, host_matches


def job(**changes):
    return Job(**({"linkedin_id": "123", "title": "Senior React Engineer", "company": "Example", "description": "React TypeScript frontend developer", "country": "Canada", "workplace": "remote"} | changes))


def test_remote_does_not_ignore_country_eligibility():
    result = match_job(job(), Search(countries=["Germany"]), Profile())
    assert not result.eligible
    assert "country" in " ".join(result.reasons).lower()


def test_required_skills_and_aliases():
    search = Search(required_skills=["React.js", "TypeScript"], min_score=0)
    assert match_job(job(), search, Profile()).eligible
    assert not match_job(job(description="Python Django"), search, Profile()).eligible


def test_missing_salary_and_mismatched_units_are_not_silently_accepted():
    search = Search(min_salary=80000, salary_currency="USD", salary_period="year", min_score=0)
    assert not match_job(job(), search, Profile()).eligible
    assert not match_job(job(salary_min=90000, salary_currency="CAD", salary_period="year"), search, Profile()).eligible
    assert match_job(job(salary_min=90000, salary_currency="USD", salary_period="year"), search, Profile()).eligible


def test_cv_selection_uses_content_and_preserves_original_version():
    resumes = [Resume(id="py", label="Python", text="Python Django backend", path="/a.pdf"), Resume(id="web", label="Frontend", text="React TypeScript frontend developer", path="/b.pdf")]
    choice = select_resume(job(), resumes, minimum=20)
    assert choice.resume.id == "web"
    assert select_resume(job(description="Rust embedded firmware", title="Rust Engineer"), resumes, minimum=70) is None


def test_answers_never_guess_unknown_work_authorization_or_years():
    profile = Profile(first_name="Jhon", email="person@example.com", skill_years={"python": 4})
    assert resolve_answer("Email address", profile).value == "person@example.com"
    assert resolve_answer("How many years of experience with Python?", profile).value == "4"
    assert resolve_answer("How many years of experience with Java?", profile) is None
    assert resolve_answer("Are you legally authorized to work in Canada?", profile) is None


def test_saved_answers_are_exact_and_country_specific():
    p = Profile(authorization={"Canada": True, "Germany": False}, saved_answers={"Are you willing to travel?": "Yes"})
    assert resolve_answer("Are you legally authorized to work in Canada?", p).value == "Yes"
    assert resolve_answer("Are you legally authorized to work in Germany?", p).value == "No"
    assert resolve_answer("Are you willing to travel?", p).value == "Yes"
    assert resolve_answer("Are you willing to travel internationally every week?", p) is None


@pytest.mark.parametrize("url", ["http://localhost/a", "http://127.0.0.1", "http://169.254.169.254/latest", "file:///etc/passwd", "https://example.com@127.0.0.1", "http://[::1]", "http://10.0.0.1"])
def test_unsafe_destinations_are_rejected(url):
    assert not public_url(url, resolve=False)


def test_domain_matching_does_not_accept_substring_impersonation():
    assert host_matches("jobs.example.com", "example.com")
    assert not host_matches("example.com.evil.test", "example.com")
    assert not host_matches("notexample.com", "example.com")
