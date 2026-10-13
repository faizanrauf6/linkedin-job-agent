import pytest

from agent.models import Job, Profile, Resume, Settings
from agent.verification import verify_destination
from agent.forms import fill_form, choose_option


@pytest.mark.asyncio
async def test_known_ats_needs_employer_careers_link():
    job = Job(linkedin_id="1", title="Frontend Engineer", company="Acme", application_url="https://jobs.lever.co/acme/123", company_website="https://acme.example")
    async def unrelated(url):
        return '<html><a href="/careers">Careers</a></html>' if url == "https://acme.example" else "No matching vacancies"
    result = await verify_destination(job, Settings(trusted_employers={"Acme": "acme.example"}), fetch=unrelated)
    assert result.state == "unresolved"
    async def corroborated(url):
        return '<a href="https://jobs.lever.co/acme/123">Frontend Engineer</a>'
    result = await verify_destination(job, Settings(trusted_employers={"Acme": "acme.example"}), fetch=corroborated)
    assert result.state == "eligible"
    assert "jobs.lever.co" in result.allowed_hosts


@pytest.mark.asyncio
async def test_payment_requests_are_blocked_before_any_fetch():
    async def should_not_fetch(url):
        raise AssertionError("Should reject before navigating")
    job = Job(linkedin_id="1", title="Engineer", company="Acme", description="Pay to apply with an application fee", easy_apply=True)
    assert (await verify_destination(job, Settings(), fetch=should_not_fetch)).state == "blocked"


def test_dropdown_matching_does_not_guess():
    assert choose_option("Yes", ["Select an option", "Yes", "No"]) == "Yes"
    assert choose_option("4", ["0-2 years", "3-5 years", "6+ years"]) == "3-5 years"
    assert choose_option("Canada", ["United States", "Germany"]) is None


@pytest.mark.asyncio
async def test_real_form_upload_and_unknown_required_question(tmp_path):
    from playwright.async_api import async_playwright
    cv = tmp_path / "frontend.pdf"
    cv.write_bytes(b"fixture resume")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''<form>
          <label>First name<input name="first" required></label>
          <label>Email<input type="email" required></label>
          <label>Resume<input type="file" required></label>
          <label>How many years of experience with Rust?<input type="number" required></label>
          <button type="submit">Submit application</button>
        </form>''')
        outcome = await fill_form(page.locator("form"), Profile(first_name="Jhon", email="a@example.com"), Resume(label="Frontend", path=str(cv)))
        assert await page.locator('[name="first"]').input_value() == "Jhon"
        assert "frontend.pdf" in await page.locator('[type="file"]').input_value()
        assert outcome.missing == ["How many years of experience with Rust?"]
        assert await page.locator('[type="number"]').input_value() == ""
        await browser.close()
