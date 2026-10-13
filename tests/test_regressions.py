import pytest

from agent.answers import resolve_answer
from agent.matching import match_job
from agent.models import Config, Job, Profile, Resume, Search, Settings
from agent.store import Store
from agent.verification import verify_destination


def test_explicit_unknown_country_never_uses_job_country_answer():
    p = Profile(authorization={"Canada": True})
    assert resolve_answer("Are you authorized to work in Germany?", p, country="Canada") is None


def test_context_free_saved_authorization_is_not_global():
    p = Profile(saved_answers={"Are you authorized to work?": "Yes"})
    assert resolve_answer("Are you authorized to work?", p, country="Germany") is None


def test_saved_answers_cannot_sign_attestations():
    p = Profile(saved_answers={"Electronic signature": "Jhon", "I certify all information is accurate": "Yes"})
    assert resolve_answer("Electronic signature", p) is None
    assert resolve_answer("I certify all information is accurate", p) is None


def test_description_residency_conflict_is_not_auto_eligible():
    j = Job(linkedin_id="1", title="Engineer", company="Acme", country="Canada", workplace="remote", description="Remote role restricted to residents of Germany. Applicants must be authorized in Germany.")
    assert not match_job(j, Search(countries=["Canada"], min_score=0), Profile(country="Canada")).eligible


def test_ambiguous_same_role_multiple_routes_require_review(tmp_path):
    store = Store(tmp_path / "agent.db")
    store.add_job(Job(linkedin_id="1", title="Engineer", company="Acme", country="Canada", easy_apply=True))
    second = store.add_job(Job(linkedin_id="2", title="Engineer", company="Acme", country="Canada", application_url="https://jobs.lever.co/acme/123"))
    assert second.status == "needs_input"
    assert "duplicate" in second.error.lower()


def test_job_actions_cannot_overwrite_a_claimed_application(tmp_path):
    store = Store(tmp_path / "agent.db")
    j = store.add_job(Job(linkedin_id="1", title="Engineer", company="Acme", status="queued"))
    store.claim()
    with pytest.raises(ValueError):
        store.job_action(j.id, "skip")
    assert store.job(j.id).status == "applying"


def test_review_mode_guard_requires_explicit_job_approval(tmp_path):
    store = Store(tmp_path / "agent.db")
    c = store.config(); c.settings.paused = False
    c = store.save_config(c)
    j = store.add_job(Job(linkedin_id="1", title="Engineer", company="Acme", status="queued"))
    store.claim()
    assert not store.begin_submission(j.id, c.revision)


def test_review_approval_survives_pause_but_not_profile_changes(tmp_path):
    store = Store(tmp_path / "agent.db")
    c = store.config()
    j = store.add_job(Job(linkedin_id="1", title="Engineer", company="Acme", status="awaiting_review"))
    store.job_action(j.id, "queue")
    c.settings.paused = False
    c = store.save_config(c)
    assert store.job(j.id).approved_revision == c.revision
    c.profile.first_name = "Changed"
    c = store.save_config(c)
    assert store.job(j.id).approved_revision != c.revision


def test_unconfirming_selected_cv_cancels_submission(tmp_path):
    store = Store(tmp_path / "agent.db")
    c = store.config(); c.settings.paused = False; c.settings.mode = "automatic"
    c = store.save_config(c)
    resume = Resume(label="CV", text="React", confirmed=True, sha256="abc")
    store.save_resume(resume)
    j = store.add_job(Job(linkedin_id="1", title="Engineer", company="Acme", status="queued", resume_id=resume.id, resume_hash="abc"))
    store.claim()
    resume.confirmed = False; store.save_resume(resume)
    assert not store.begin_submission(j.id, c.revision)


@pytest.mark.asyncio
async def test_easy_apply_requires_linkedin_company_binding():
    job = Job(linkedin_id="1", title="Engineer", company="Acme", easy_apply=True, company_website="https://acme.example", company_linkedin_url="https://www.linkedin.com/company/impersonator/")
    async def website(url):
        return '<p>Engineer</p><a href="https://www.linkedin.com/company/acme/">LinkedIn</a>'
    result = await verify_destination(job, Settings(trusted_employers={"Acme":"acme.example"}), fetch=website)
    assert result.state == "unresolved"


@pytest.mark.asyncio
async def test_easy_apply_with_corroborated_company_identity_and_role_passes():
    job = Job(linkedin_id="1", title="Engineer", company="Acme", easy_apply=True, company_website="https://acme.example", company_linkedin_url="https://www.linkedin.com/company/acme/")
    async def website(url):
        return '<p>Engineer</p><a href="https://www.linkedin.com/company/acme/">LinkedIn</a>'
    assert (await verify_destination(job, Settings(trusted_employers={"Acme":"acme.example"}), fetch=website)).state == "eligible"


@pytest.mark.asyncio
async def test_required_non_cv_upload_is_not_given_a_cv(tmp_path):
    from playwright.async_api import async_playwright
    from agent.forms import fill_form
    cv = tmp_path / "cv.pdf"; cv.write_bytes(b"fixture")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content('<form><label>Passport or national ID<input type="file" required></label></form>')
        result = await fill_form(page.locator('form'), Profile(), Resume(label="CV", path=str(cv)))
        assert not result.uploaded
        assert await page.locator('input').input_value() == ""
        assert result.missing
        await browser.close()


@pytest.mark.asyncio
async def test_paused_guard_stops_before_first_fill():
    from playwright.async_api import async_playwright
    from agent.forms import fill_form
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content('<form><label>First name<input required></label></form>')
        result = await fill_form(page.locator('form'), Profile(first_name="Jhon"), Resume(label="CV"), can_act=lambda:False)
        assert await page.locator('input').input_value() == ""
        assert result.missing == ["Agent paused or configuration changed"]
        await browser.close()
