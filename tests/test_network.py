import pytest

from agent.browser import BrowserAgent
from agent.verification import fetch_html


class Request:
    def __init__(self, url, method="GET"):
        self.url, self.method = url, method
    def is_navigation_request(self):
        return True


class Response:
    def __init__(self, status, target):
        self.status, self.headers = status, {"location": target}
        self.disposed = False
    async def dispose(self):
        self.disposed = True


class Route:
    """A transport boundary recording whether a request is released or blocked."""
    def __init__(self, response, method="GET"):
        self.request = Request("https://careers.example/apply", method)
        self.response, self.result, self.body = response, "", ""
    async def fetch(self, **options):
        assert options["max_redirects"] == 0
        return self.response
    async def abort(self):
        self.result = "blocked"
    async def fulfill(self, **options):
        self.result = "released"
        self.body = options.get("body", "")


@pytest.mark.asyncio
async def test_post_redirect_never_forwards_application_body_to_another_host(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.browser.public_url", lambda url: True)
    agent = BrowserAgent(tmp_path)
    agent.sensitive_hosts = {"careers.example"}
    route = Route(Response(307, "https://evil.example/collect"), method="POST")
    await agent._route(route)
    assert route.result == "blocked"
    assert route.response.disposed


@pytest.mark.asyncio
async def test_same_host_get_redirect_becomes_a_separately_checked_navigation(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.browser.public_url", lambda url: True)
    agent = BrowserAgent(tmp_path)
    agent.sensitive_hosts = {"careers.example"}
    route = Route(Response(302, "/application"))
    await agent._route(route)
    assert route.result == "released"
    assert "https://careers.example/application" in route.body
    assert route.response.disposed


@pytest.mark.asyncio
async def test_fetched_resource_is_disposed_after_fulfill(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.browser.public_url", lambda url: True)
    route = Route(Response(200, ""))
    await BrowserAgent(tmp_path)._route(route)
    assert route.result == "released"
    assert route.response.disposed


@pytest.mark.asyncio
async def test_careers_fetch_rejects_cross_company_redirect(monkeypatch):
    import httpx
    monkeypatch.setattr("agent.verification.public_url", lambda url: True)
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            class Stream:
                async def __aenter__(self):
                    return httpx.Response(302, headers={"location":"https://evil.example"}, request=httpx.Request("GET","https://acme.example"))
                async def __aexit__(self, *args): pass
            return Stream()
    monkeypatch.setattr("agent.verification.httpx.AsyncClient", lambda **kwargs: Client())
    with pytest.raises(ValueError, match="outside the employer"):
        await fetch_html("https://acme.example")


@pytest.mark.asyncio
async def test_sensitive_page_is_destroyed_before_reopening_network(tmp_path):
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()
        await page.set_content('<input value="private applicant data">')
        agent = BrowserAgent(tmp_path)
        agent.context, agent.page = context, page
        agent.sensitive_hosts = {"careers.example"}
        await agent._reset_sensitive()
        assert page.url == "about:blank"
        assert await page.locator("input").count() == 0
        assert agent.sensitive_hosts is None
        await browser.close()


@pytest.mark.asyncio
async def test_preexisting_success_text_is_not_a_new_confirmation(tmp_path):
    from playwright.async_api import async_playwright
    from agent.browser import CONFIRMATION
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content('<p>Thank you for applying.</p><form><input required></form>')
        agent = BrowserAgent(tmp_path); agent.page = page
        prior = await agent.confirmations()
        assert prior == {"Thank you for applying."}
        assert not await agent.has_new_confirmation(prior)
        await page.set_content('<div role="status">Application submitted successfully!</div>')
        assert await agent.has_new_confirmation(prior)
        await browser.close()
