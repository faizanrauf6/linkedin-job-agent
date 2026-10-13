"""Offline ownership and resource-bound regressions for careers evidence."""
import httpx
import pytest

from agent.models import Job, Settings
from agent.verification import fetch_html, verify_destination


OFFICIAL = "https://acme.example"


def vacancy(target="https://jobs.lever.co/acme/123", **changes):
    return Job(**({"linkedin_id": "1", "title": "Engineer", "company": "Acme",
                  "company_website": OFFICIAL, "application_url": target} | changes))


def site(pages, visited=None):
    async def fetch(url):
        if visited is not None:
            visited.append(url)
        assert url in pages, f"Unexpected evidence request: {url}"
        return pages[url]
    return fetch


@pytest.mark.parametrize("board,target", [
    ("https://jobs.lever.co/acme", "https://jobs.lever.co/acme/123"),
    ("https://job-boards.greenhouse.io/acme", "https://job-boards.greenhouse.io/acme/jobs/123"),
    ("https://boards.greenhouse.io/embed/job_board?for=acme", "https://boards.greenhouse.io/embed/job_app?for=acme&token=123"),
])
async def test_official_careers_link_to_supported_board_proves_exact_vacancy(board, target):
    pages = {OFFICIAL: '<a href="/careers">Careers</a>',
             OFFICIAL + "/careers": f'<a href="{board}">Open roles</a>',
             board: f'<a href="{target}">Engineer</a>'}
    result = await verify_destination(vacancy(target), Settings(), fetch=site(pages))
    assert result.state == "eligible"
    assert any(OFFICIAL + "/careers" in item and board in item for item in result.evidence)
    assert any(board in item and target in item for item in result.evidence)


async def test_careers_links_beyond_first_three_and_nested_pages_are_inspected():
    target = "https://jobs.lever.co/acme/123"
    pages = {OFFICIAL: ''.join(f'<a href="/careers/{i}">Careers</a>' for i in range(5)),
             **{OFFICIAL + f"/careers/{i}": "No vacancies" for i in range(4)},
             OFFICIAL + "/careers/4": '<a href="/careers/engineering">Engineering</a>',
             OFFICIAL + "/careers/engineering": f'<a href="{target}">Engineer</a>'}
    assert (await verify_destination(vacancy(), Settings(), fetch=site(pages))).state == "eligible"


@pytest.mark.parametrize("bad", ["https://jobs.lever.co/other/123", "https://evil.example/123"])
async def test_linked_board_cannot_authorize_another_tenant_or_origin(bad):
    board = "https://jobs.lever.co/acme"
    pages = {OFFICIAL: f'<a href="{board}">Jobs</a>', board: f'<a href="{bad}">Engineer</a>'}
    visited = []
    result = await verify_destination(vacancy(bad), Settings(), fetch=site(pages, visited))
    assert result.state == "unresolved"
    assert bad not in visited
    assert not result.allowed_hosts
    assert any(OFFICIAL in item and board in item for item in result.evidence)


async def test_shared_ats_homepage_does_not_establish_tenant_ownership():
    visited = []
    pages = {OFFICIAL: '<a href="https://jobs.lever.co">Careers</a>'}
    result = await verify_destination(vacancy(), Settings(), fetch=site(pages, visited))
    assert result.state == "unresolved"
    assert visited == [OFFICIAL]


async def test_js_rendered_official_link_and_board_supply_provenance():
    board = "https://jobs.lever.co/acme"
    pages = {OFFICIAL: '<script src="/careers.js"></script>', board: '<script src="/jobs.js"></script>'}
    rendered = {OFFICIAL: f'<a href="{board}">Careers</a>', board: '<a href="/acme/123">Engineer</a>'}
    calls = []
    result = await verify_destination(vacancy(), Settings(), fetch=site(pages), render=site(rendered, calls))
    assert result.state == "eligible"
    assert calls == [OFFICIAL, board]
    assert any("render" in item.casefold() and OFFICIAL in item for item in result.evidence)


async def test_renderer_is_optional_and_failure_leaves_destination_unresolved():
    async def render(url):
        raise RuntimeError("Browser unavailable")
    result = await verify_destination(vacancy(), Settings(), fetch=site({OFFICIAL: '<script src="/app.js"></script>'}), render=render)
    assert result.state == "unresolved"
    assert not result.allowed_hosts


async def test_easy_apply_board_role_still_requires_exact_linkedin_company_binding():
    board = "https://jobs.lever.co/acme"
    pages = {OFFICIAL: f'<a href="https://www.linkedin.com/company/acme/">LinkedIn</a><a href="{board}">Careers</a>',
             board: '<p>Engineer</p>'}
    job = vacancy(easy_apply=True, company_linkedin_url="https://www.linkedin.com/company/acme/")
    assert (await verify_destination(job, Settings(), fetch=site(pages))).state == "eligible"
    job.company_linkedin_url = "https://www.linkedin.com/company/other/"
    assert (await verify_destination(job, Settings(), fetch=site(pages))).state == "unresolved"


async def test_script_payload_is_not_visible_role_evidence_for_easy_apply():
    html = '<a href="https://www.linkedin.com/company/acme/">LinkedIn</a><script>const role = "Engineer";</script>'
    job = vacancy(easy_apply=True, company_linkedin_url="https://www.linkedin.com/company/acme/")
    assert (await verify_destination(job, Settings(), fetch=site({OFFICIAL: html}))).state == "unresolved"


async def test_oversized_fetch_document_cannot_establish_ownership():
    html = "x" * 1_500_001 + '<a href="https://jobs.lever.co/acme/123">Engineer</a>'
    result = await verify_destination(vacancy(), Settings(), fetch=site({OFFICIAL: html}))
    assert result.state == "unresolved"
    assert not result.allowed_hosts


async def test_cyclic_and_wide_careers_graph_has_a_finite_fetch_budget():
    visited = []
    async def fetch(url):
        visited.append(url)
        return '<a href="/">Home</a>' + ''.join(f'<a href="/careers/{i}">Jobs</a>' for i in range(100))
    result = await verify_destination(vacancy(), Settings(), fetch=fetch)
    assert result.state == "unresolved"
    assert len(visited) <= 12
    assert len(set(visited)) == len(visited)


async def test_careers_fetch_rejects_shared_host_tenant_redirect(monkeypatch):
    monkeypatch.setattr("agent.verification.public_url", lambda url: True)
    transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "https://jobs.lever.co/other"}))
    client_class = httpx.AsyncClient
    monkeypatch.setattr("agent.verification.httpx.AsyncClient", lambda **kwargs: client_class(transport=transport, **kwargs))
    with pytest.raises(ValueError, match="tenant"):
        await fetch_html("https://jobs.lever.co/acme")


async def test_embedded_shared_ats_host_without_a_tenant_receives_no_applicant_access():
    pages = {OFFICIAL: '<a href="/apply">Engineer</a>',
             OFFICIAL + "/apply": '<iframe src="https://jobs.lever.co"></iframe>'}
    result = await verify_destination(vacancy(OFFICIAL + "/apply"), Settings(), fetch=site(pages))
    assert result.state == "eligible"
    assert "jobs.lever.co" not in result.allowed_hosts


async def test_rendered_board_cannot_authorize_a_different_tenant():
    board = "https://jobs.lever.co/acme"
    pages = {OFFICIAL: f'<a href="{board}">Careers</a>', board: '<script src="/jobs.js"></script>'}
    result = await verify_destination(vacancy("https://jobs.lever.co/other/123"), Settings(), fetch=site(pages),
                                      render=site({board: '<a href="/other/123">Engineer</a>'}))
    assert result.state == "unresolved"
    assert not result.allowed_hosts


async def test_oversized_rendered_document_cannot_establish_ownership():
    async def render(url):
        return "x" * 1_500_001 + '<a href="https://jobs.lever.co/acme/123">Engineer</a>'
    result = await verify_destination(vacancy(), Settings(), fetch=site({OFFICIAL: '<script src="/app.js"></script>'}), render=render)
    assert result.state == "unresolved"
    assert not result.allowed_hosts


async def test_exact_static_proof_avoids_browser_rendering():
    async def render(url):
        pytest.fail("Static ownership proof should not require rendering")
    html = '<script src="/app.js"></script><a href="https://jobs.lever.co/acme/123">Engineer</a>'
    result = await verify_destination(vacancy(), Settings(), fetch=site({OFFICIAL: html}), render=render)
    assert result.state == "eligible"


async def test_render_attempts_and_depth_are_bounded():
    fetched, rendered = [], []
    async def fetch(url):
        fetched.append(url)
        path = url.removeprefix(OFFICIAL)
        return '<script src="/app.js"></script><a href="' + path + '/careers">Careers</a>'
    async def render(url):
        rendered.append(url)
        return '<script src="/app.js"></script>'
    result = await verify_destination(vacancy(), Settings(), fetch=fetch, render=render)
    assert result.state == "unresolved"
    assert len(fetched) <= 5
    assert len(rendered) <= 4
    assert len(set(rendered)) == len(rendered)


async def test_malformed_incidental_link_does_not_hide_valid_official_proof():
    html = '<a href="http://[">Broken link</a><a href="https://jobs.lever.co/acme/123">Engineer</a>'
    assert (await verify_destination(vacancy(), Settings(), fetch=site({OFFICIAL: html}))).state == "eligible"
