"""Local HTTP fixtures exercise the isolated evidence browser without accounts."""
import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from playwright.async_api import Route, async_playwright

from agent.evidence_browser import render_evidence


@pytest.fixture
def evidence_server():
    requests = []
    documents = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.command, self.headers.get("Cookie", "")))
            document = documents.get(self.path, (200, {}, "<p>Evidence</p>"))
            status, headers, body = document[:3]
            if len(document) == 4:
                time.sleep(document[3])
            self.send_response(status)
            for name, value in ({"Content-Type": "text/html"} | headers).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):
            requests.append((self.path, self.command, self.headers.get("Cookie", "")))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", documents, requests
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def public_fixture_server(monkeypatch):
    monkeypatch.setattr("agent.evidence_browser.public_url", lambda url: urlsplit(url).hostname in {"127.0.0.1", "localhost"})


async def test_javascript_evidence_has_no_applicant_cookies_and_browser_is_closed(evidence_server, public_fixture_server, monkeypatch):
    base, documents, requests = evidence_server
    documents["/careers"] = (200, {}, '''<p id="result"></p><script>
      setTimeout(() => document.getElementById('result').innerHTML =
        '<a href="https://jobs.lever.co/acme/123">Engineer</a>' + document.cookie, 30);
      fetch('/collect', {method:'POST', body:'must not send'});
      window.open('/popup');
      new WebSocket(location.origin.replace('http:', 'ws:') + '/socket');
      navigator.serviceWorker.register('/worker.js').catch(() => {});
      </script>''')
    async with async_playwright() as pw:
        applicant = await pw.chromium.launch(headless=True)
        context = await applicant.new_context()
        await context.add_cookies([{"name": "applicant", "value": "private", "url": base}])
        launched = []
        launch = pw.chromium.launch
        async def tracked_launch(**kwargs):
            browser = await launch(**kwargs)
            launched.append(browser)
            return browser
        monkeypatch.setattr(pw.chromium, "launch", tracked_launch)
        try:
            html = await render_evidence(pw, base + "/careers")
            assert '<a href="https://jobs.lever.co/acme/123">Engineer</a>' in html
            assert "private" not in html
            assert requests == [("/careers", "GET", "")]
            assert launched and not launched[0].is_connected()
            assert applicant.is_connected()
        finally:
            await applicant.close()


async def test_http_redirect_outside_document_owner_is_blocked(evidence_server, public_fixture_server):
    base, documents, requests = evidence_server
    documents["/careers"] = (302, {"Location": base.replace("127.0.0.1", "localhost") + "/other-company"}, "")
    async with async_playwright() as pw:
        with pytest.raises(ValueError, match="redirect"):
            await render_evidence(pw, base + "/careers")
    assert requests == [("/careers", "GET", "")]


async def test_redirect_to_private_destination_is_blocked_before_request(evidence_server, public_fixture_server):
    base, documents, requests = evidence_server
    documents["/careers"] = (302, {"Location": base.replace("127.0.0.1", "127.0.0.2") + "/private"}, "")
    async with async_playwright() as pw:
        with pytest.raises(ValueError, match="redirect"):
            await render_evidence(pw, base + "/careers")
    assert requests == [("/careers", "GET", "")]


async def test_same_owner_redirect_and_public_script_resource_render(evidence_server, public_fixture_server):
    base, documents, requests = evidence_server
    documents["/careers"] = (302, {"Location": "/jobs"}, "")
    documents["/jobs"] = (200, {}, '<p id="role"></p><script src="' + base.replace("127.0.0.1", "localhost") + '/app.js"></script>')
    documents["/app.js"] = (200, {"Content-Type": "text/javascript"}, 'document.querySelector("#role").textContent="Engineer";')
    async with async_playwright() as pw:
        html = await render_evidence(pw, base + "/careers")
    assert '<p id="role">Engineer</p>' in html
    assert {path for path, _, _ in requests} == {"/careers", "/jobs", "/app.js"}


async def test_oversized_html_is_rejected_and_isolated_browser_closed(evidence_server, public_fixture_server, monkeypatch):
    base, documents, _ = evidence_server
    documents["/careers"] = (200, {}, "x" * 1_500_001)
    async with async_playwright() as pw:
        launched = []
        launch = pw.chromium.launch
        async def tracked_launch(**kwargs):
            browser = await launch(**kwargs)
            launched.append(browser)
            return browser
        monkeypatch.setattr(pw.chromium, "launch", tracked_launch)
        with pytest.raises(ValueError, match="inspection limit"):
            await render_evidence(pw, base + "/careers")
        assert launched and not launched[0].is_connected()


@pytest.mark.parametrize("destination,permitted", [("/acme/123", True), ("/other/123", False)])
async def test_shared_ats_hostname_redirect_preserves_exact_tenant(evidence_server, monkeypatch, destination, permitted):
    base, documents, requests = evidence_server
    documents["/acme"] = (302, {"Location": destination}, "")
    documents["/acme/123"] = (200, {}, "<p>Engineer</p>")
    monkeypatch.setattr("agent.evidence_browser.public_url", lambda url: urlsplit(url).hostname == "jobs.lever.co")
    fetch = Route.fetch
    async def local_transport(route, **kwargs):
        # Only the HTTP transport maps to localhost; the actual browser URL,
        # redirect handling and tenant checks use the supported ATS hostname.
        kwargs["url"] = base + urlsplit(kwargs.get("url", route.request.url)).path
        return await fetch(route, **kwargs)
    monkeypatch.setattr(Route, "fetch", local_transport)
    async with async_playwright() as pw:
        url = f"http://jobs.lever.co:{urlsplit(base).port}/acme"
        if permitted:
            assert "<p>Engineer</p>" in await render_evidence(pw, url)
            assert [path for path, _, _ in requests] == ["/acme", "/acme/123"]
        else:
            with pytest.raises(ValueError, match="tenant"):
                await render_evidence(pw, url)
            assert [path for path, _, _ in requests] == ["/acme"]


async def test_private_subresource_redirect_is_blocked(evidence_server, public_fixture_server):
    base, documents, requests = evidence_server
    documents["/careers"] = (200, {}, '<p>Engineer</p><script src="/app.js"></script>')
    documents["/app.js"] = (302, {"Location": base.replace("127.0.0.1", "127.0.0.2") + "/private"}, "")
    async with async_playwright() as pw:
        assert "<p>Engineer</p>" in await render_evidence(pw, base + "/careers")
    assert [path for path, _, _ in requests] == ["/careers", "/app.js"]


async def test_javascript_navigation_cannot_change_document_owner(evidence_server, public_fixture_server):
    base, documents, requests = evidence_server
    destination = base.replace("127.0.0.1", "localhost") + "/other-company"
    documents["/careers"] = (200, {}, '<script>location.href = "' + destination + '";</script>')
    async with async_playwright() as pw:
        with pytest.raises(ValueError, match="ownership"):
            await render_evidence(pw, base + "/careers")
    assert requests == [("/careers", "GET", "")]


async def test_every_http_document_redirect_hop_is_checked(evidence_server, public_fixture_server):
    base, documents, requests = evidence_server
    documents["/careers"] = (302, {"Location": "/step"}, "")
    documents["/step"] = (302, {"Location": base.replace("127.0.0.1", "localhost") + "/other-company"}, "")
    async with async_playwright() as pw:
        with pytest.raises(ValueError):
            await render_evidence(pw, base + "/careers")
    assert [path for path, _, _ in requests] == ["/careers", "/step"]


async def test_every_resource_redirect_hop_is_checked(evidence_server, monkeypatch):
    base, documents, requests = evidence_server
    documents["/careers"] = (200, {}, '<p>Engineer</p><script src="/app.js"></script>')
    documents["/app.js"] = (302, {"Location": "/step.js"}, "")
    documents["/step.js"] = (302, {"Location": "/private"}, "")
    documents["/private"] = (200, {"Content-Type": "text/javascript"}, 'document.body.textContent="Untrusted";')
    # Classify one fixture endpoint as non-public. The earlier single-hop test
    # uses an actual private IP; this test makes an unchecked later hop visible.
    monkeypatch.setattr("agent.evidence_browser.public_url", lambda url: urlsplit(url).hostname == "127.0.0.1" and urlsplit(url).path != "/private")
    async with async_playwright() as pw:
        assert "<p>Engineer</p>" in await render_evidence(pw, base + "/careers")
    assert [path for path, _, _ in requests] == ["/careers", "/app.js", "/step.js"]


async def test_http_document_redirect_loops_have_a_finite_budget(evidence_server, public_fixture_server):
    base, documents, requests = evidence_server
    documents["/careers"] = (302, {"Location": "/careers"}, "")
    async with async_playwright() as pw:
        with pytest.raises(ValueError, match="redirect limit"):
            await render_evidence(pw, base + "/careers")
    assert len(requests) <= 6


async def test_cancelled_render_closes_its_isolated_browser(evidence_server, public_fixture_server, monkeypatch):
    base, _, requests = evidence_server
    async with async_playwright() as pw:
        launched = []
        launch = pw.chromium.launch
        async def tracked_launch(**kwargs):
            browser = await launch(**kwargs)
            launched.append(browser)
            return browser
        monkeypatch.setattr(pw.chromium, "launch", tracked_launch)
        task = asyncio.create_task(render_evidence(pw, base + "/careers"))
        try:
            for _ in range(200):
                if requests:
                    break
                await asyncio.sleep(0.01)
            assert requests
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert launched and not launched[0].is_connected()


async def test_links_resolve_against_rendered_document_after_safe_redirect(evidence_server, public_fixture_server):
    base, documents, _ = evidence_server
    documents["/careers"] = (302, {"Location": "/teams/"}, "")
    documents["/teams/"] = (200, {}, '<a href="engineer">Engineer</a>')
    async with async_playwright() as pw:
        html = await render_evidence(pw, base + "/careers")
    assert f'<a href="{base}/teams/engineer">Engineer</a>' in html


@pytest.mark.parametrize("navigation", ["redirect", "javascript"])
async def test_snapshot_waits_for_pending_document_navigation(evidence_server, public_fixture_server, navigation):
    base, documents, requests = evidence_server
    documents["/careers"] = ((302, {"Location": "/teams/"}, "") if navigation == "redirect" else
                             (200, {}, '<script>location.replace("/teams/")</script>'))
    documents["/teams/"] = (200, {}, '<a href="engineer">Engineer</a>', 3.5)
    async with async_playwright() as pw:
        html = await render_evidence(pw, base + "/careers")
    assert f'<a href="{base}/teams/engineer">Engineer</a>' in html
    assert [path for path, _, _ in requests] == ["/careers", "/teams/"]


async def test_delayed_redirect_waits_for_final_document_javascript_evidence(evidence_server, public_fixture_server):
    base, documents, _ = evidence_server
    documents["/careers"] = (302, {"Location": "/teams/"}, "")
    documents["/teams/"] = (200, {}, '<div id="roles"></div><script>fetch("/roles").then(r=>r.text()).then(html=>document.querySelector("#roles").innerHTML=html)</script>', 3.5)
    documents["/roles"] = (200, {}, '<a href="engineer">Engineer</a>', 0.3)
    async with async_playwright() as pw:
        html = await render_evidence(pw, base + "/careers")
    assert f'<a href="{base}/teams/engineer">Engineer</a>' in html
