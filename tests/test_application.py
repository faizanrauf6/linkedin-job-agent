import pytest
from playwright.async_api import async_playwright

from agent.browser import BrowserAgent
from agent.forms import fill_form
from agent.models import Config, Job, Profile, Resume, Verification


@pytest.mark.asyncio
async def test_login_challenges_require_intervention(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        agent = BrowserAgent(tmp_path)
        agent.page = await browser.new_page()
        await agent.page.set_content("<body>Security verification: verify you are human</body>")
        assert await agent._challenge()
        await agent.page.set_content('<body><iframe src="about:blank#captcha"></iframe></body>')
        assert await agent._challenge()
        await agent.page.set_content("<body>Application form</body>")
        assert not await agent._challenge()
        await browser.close()


@pytest.mark.asyncio
async def test_explicit_demographic_decline_preference(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''<form><label>Gender<select required>
            <option value="">Choose</option><option>Woman</option><option>Man</option>
            <option>Prefer not to answer</option></select></label></form>''')
        result = await fill_form(page.locator("form"), Profile(demographic_preference="decline"), Resume(label="CV"))
        assert not result.missing
        assert await page.locator("select").input_value() == "Prefer not to answer"
        assert result.sources["Gender"] == "profile:demographic_preference"
        await page.locator("select").select_option(value="")
        result = await fill_form(page.locator("form"), Profile(), Resume(label="CV"))
        assert result.missing == ["Gender"]
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown_question", [False, True])
async def test_application_flow_reserves_before_submit_and_requires_confirmation(tmp_path, unknown_question):
    cv = tmp_path / "immutable.pdf"
    cv.write_bytes(b"fixture document")
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(str(tmp_path / "browser"), headless=True)
        page = context.pages[0]
        question = '<label>Unknown personal fact<input required></label>' if unknown_question else ''
        form = '''<form onsubmit="event.preventDefault();this.remove();document.body.innerHTML='<h1>Application submitted successfully!</h1>'">
            <label>First name<input name="first_name" required></label>
            <label>Email<input type="email" required></label>
            <label>Resume<input type="file" required></label>''' + question + '<button>Submit application</button></form>'
        await page.route("https://careers.example/**", lambda route: route.fulfill(content_type="text/html", body=form))
        browser = BrowserAgent(tmp_path)
        browser.context, browser.page = context, page
        async def verify(*args):
            return Verification(state="eligible", allowed_hosts=["careers.example"])
        browser.verify = verify
        reserved = []
        result = await browser.apply(
            Job(linkedin_id="fixture", title="Engineer", company="Example", application_url="https://careers.example/apply"),
            Profile(first_name="Alex", email="alex@example.com"),
            Resume(label="CV", filename="Alex CV.pdf", path=str(cv)), Config(),
            lambda: reserved.append(True) or True,
        )
        assert result.state == ("needs_input" if unknown_question else "submitted")
        assert reserved == ([] if unknown_question else [True])
        if unknown_question:
            assert result.questions == ["Unknown personal fact"]
            assert await page.locator('input[type="file"]').input_value() == "C:\\fakepath\\Alex CV.pdf"
        await context.close()
