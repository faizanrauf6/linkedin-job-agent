"""Offline synthetic semantic fixtures, never live portal certification."""
import json
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from agent.adapters import adapter_for
from agent.browser import BrowserAgent
from agent.models import Config, Job, Profile, Resume, Settings, Verification
from agent.verification import ats_tenant, verify_destination

# Literal examples define supported ownership boundaries; they are not captured
# production HTML and make no claim about a vendor's current complete UI.
PORTALS = [
    ('workday', 'https://acme.wd5.myworkdayjobs.com/en-US/External', 'https://acme.wd5.myworkdayjobs.com/en-US/External/job/Engineer_R123', 'https://acme.wd5.myworkdayjobs.com/en-US/Internal/job/Engineer_R123', 'data-automation-id="applyFlowPage"', 'Save and Continue', 'Submit'),
    ('icims', 'https://careers-acme.icims.com/jobs', 'https://careers-acme.icims.com/jobs/123/engineer/job', 'https://careers-other.icims.com/jobs/123/engineer/job', 'id="iCIMS_ApplicationForm"', 'Continue', 'Submit'),
    ('taleo', 'https://acme.taleo.net/careersection/external/jobsearch.ftl', 'https://acme.taleo.net/careersection/external/jobdetail.ftl?job=123', 'https://acme.taleo.net/careersection/internal/jobdetail.ftl?job=123', 'id="requisitionDescriptionInterface"', 'Save and Continue', 'Submit'),
    ('smartrecruiters', 'https://jobs.smartrecruiters.com/Acme', 'https://jobs.smartrecruiters.com/Acme/123-engineer', 'https://jobs.smartrecruiters.com/Other/123-engineer', 'data-test="application-form"', 'Next', 'Submit application'),
    ('ashby', 'https://jobs.ashbyhq.com/Acme', 'https://jobs.ashbyhq.com/Acme/123', 'https://jobs.ashbyhq.com/Other/123', 'class="ashby-application-form-container"', 'Review', 'Submit Application'),
    ('bamboohr', 'https://acme.bamboohr.com/careers', 'https://acme.bamboohr.com/careers/123', 'https://other.bamboohr.com/careers/123', 'id="applicationForm"', 'Continue', 'Submit application'),
    ('successfactors', 'https://career5.successfactors.eu/career?company=Acme', 'https://career5.successfactors.eu/career?company=Acme&career_job_req_id=123', 'https://career5.successfactors.eu/career?company=Other&career_job_req_id=123', 'id="jobApplication"', 'Save and Continue', 'Apply'),
]


@pytest.mark.parametrize('name,board,target,other,markup,next_label,submit_label', PORTALS)
def test_dedicated_adapter_and_exact_tenant(name, board, target, other, markup, next_label, submit_label):
    assert adapter_for(target).name == name
    assert ats_tenant(board) is not None
    assert ats_tenant(board) == ats_tenant(target)
    assert ats_tenant(other) != ats_tenant(board)
    hostile = 'https://' + urlsplit(target).hostname + '.evil.example/apply'
    assert adapter_for(hostile).name == 'generic'


@pytest.mark.parametrize('name,board,target,other,markup,next_label,submit_label', PORTALS)
async def test_official_board_traversal_never_grants_other_tenant(name, board, target, other, markup, next_label, submit_label):
    official = 'https://acme.example'
    pages = {official: '<a href="/careers">Careers</a>', official + '/careers': f'<a href="{board}">Jobs</a>', board: f'<a href="{target}">Engineer</a><a href="{other}">Unrelated</a>'}
    visited = []
    async def fetch(url):
        visited.append(url)
        return pages.get(url, '')
    def job(url):
        return Job(linkedin_id='1', title='Engineer', company='Acme', company_website=official, application_url=url)
    eligible = await verify_destination(job(target), Settings(), fetch=fetch)
    assert eligible.state == 'eligible'
    assert eligible.allowed_hosts == ['acme.example', urlsplit(target).hostname]
    assert any(board in proof and target in proof for proof in eligible.evidence)
    rejected = await verify_destination(job(other), Settings(), fetch=fetch)
    assert rejected.state == 'unresolved'
    assert rejected.allowed_hosts == []
    assert other not in visited


@pytest.mark.parametrize('url', [
    'https://jobs.smartrecruiters.com/', 'https://jobs.ashbyhq.com/',
    'https://career5.successfactors.eu/career',
    'https://career5.successfactors.eu/career?company=Acme&company=Other',
    'https://acme.wd5.myworkdayjobs.com/',
    'https://acme.wd5.myworkdayjobs.com/en-US/External/%2e%2e/Internal',
    'https://acme.taleo.net/careersection/',
    'https://jobs.ashbyhq.com/Acme%2FOther/123',
    'https://jobs.smartrecruiters.com/api/123',
])
def test_shared_or_malformed_portal_paths_cannot_establish_tenant(url):
    assert ats_tenant(url) is None


def test_capabilities_report_fixture_scope_and_no_live_certification():
    from agent.portal_capabilities import capability_registry
    entries = capability_registry()
    json.dumps(entries)
    by_name = {entry['name']: entry for entry in entries}
    for name, *_ in PORTALS:
        entry = by_name[name]
        assert entry['status'] == 'fixture_verified'
        assert entry['live_certified'] is False
        assert 'multi_step_review_submit' in entry['supported_scopes']
        assert {'account_creation', 'captcha', 'custom_assessments'} <= set(entry['limitations'])
        assert adapter_for(next(row[2] for row in PORTALS if row[0] == name)).capability == entry


@pytest_asyncio.fixture
async def portal_browser(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        context.set_default_timeout(700)
        agent = BrowserAgent(tmp_path, headless=True)
        agent.context, agent.page = context, await context.new_page()
        async def short_wait(milliseconds):
            await __import__('asyncio').sleep(.01)
        agent.page.wait_for_timeout = short_wait
        yield agent
        await browser.close()


def journey_html(markup, next_label, submit_label):
    # A visible non-form application container forces use of the dedicated scope.
    # Each wizard replaces its DOM so attachment transfer is also exercised.
    steps = [
        f'<div {markup}><label>First name<input required></label><label>Email<input type=email required></label><label>Resume<input type=file required></label><button onclick="advance(1)">{next_label}</button></div>',
        f'<div {markup}><label>Last name<input required></label><button onclick="advance(2)">Review</button></div>',
        f'<div {markup}><h2>Review your application</h2><button onclick="document.body.innerHTML=\'Application submitted successfully\'">{submit_label}</button></div>',
    ]
    return '<div id="wizard"></div><script>const steps=' + json.dumps(steps) + ';function advance(i){document.querySelector("#wizard").innerHTML=steps[i]}advance(0)</script>'


@pytest.mark.parametrize('name,board,target,other,markup,next_label,submit_label', PORTALS)
async def test_browser_full_multistep_review_and_explicit_submit(portal_browser, tmp_path, name, board, target, other, markup, next_label, submit_label):
    agent = portal_browser
    async def route(request_route):
        if request_route.request.url == target:
            await request_route.fulfill(content_type='text/html', body=journey_html(markup, next_label, submit_label))
        else:
            await request_route.abort()
    await agent.context.route('**/*', route)
    # Only ownership/network evidence is replaced. DOM, form filling, document
    # upload proof, review continuation, and submission gates are production code.
    async def verified(job, config):
        return Verification(state='eligible', allowed_hosts=[urlsplit(target).hostname])
    agent.verify = verified
    path = tmp_path / 'selected.pdf'
    path.write_bytes(b'offline selected resume')
    import hashlib
    resume = Resume(label='Selected', filename=path.name, path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), confirmed=True)
    job = Job(linkedin_id='1', title='Engineer', company='Acme', application_url=target)
    gates = []
    def before_submit():
        gates.append('explicit-submit')
        return True
    profile = Profile(first_name='Ada', last_name='Lovelace', email='ada@example.com')
    config = Config()
    prepared = await agent.apply(job, profile, resume, config, before_submit, dry_run=True)
    assert prepared.state == 'needs_input' and 'ready for review' in prepared.reason.lower(), prepared
    assert gates == []
    assert job.adapter_name == name
    assert prepared.answers['First name'] == 'Ada'
    assert prepared.answers['Last name'] == 'Lovelace'
    assert await agent.page.get_by_role('heading', name='Review your application').is_visible()
    submitted = await agent.apply(job, profile, resume, config, before_submit)
    assert submitted.state == 'submitted', submitted
    assert gates == ['explicit-submit']
    assert await agent.page.get_by_text('Application submitted successfully', exact=True).is_visible()


@pytest.mark.parametrize('board,other_case', [
    ('https://jobs.ashbyhq.com/Acme', 'https://jobs.ashbyhq.com/acme/123'),
    ('https://jobs.smartrecruiters.com/Acme', 'https://jobs.smartrecruiters.com/acme/123'),
    ('https://acme.wd5.myworkdayjobs.com/en-US/External', 'https://acme.wd5.myworkdayjobs.com/en-US/external/job/123'),
    ('https://acme.taleo.net/careersection/External', 'https://acme.taleo.net/careersection/external/jobdetail.ftl?job=123'),
    ('https://career5.successfactors.eu/career?company=Acme', 'https://career5.successfactors.eu/career?company=acme&career_job_req_id=123'),
])
def test_path_and_query_tenants_are_exact_without_assuming_vendor_case_rules(board, other_case):
    assert ats_tenant(board) != ats_tenant(other_case)


@pytest.mark.parametrize('name,board,target,other,markup,next_label,submit_label', PORTALS)
async def test_dedicated_scope_ignores_newsletter_and_entry_cannot_switch_tenant(portal_browser, name, board, target, other, markup, next_label, submit_label):
    agent = portal_browser
    async def route(request_route):
        await request_route.fulfill(content_type='text/html', body=f'<form aria-label="Newsletter"><label>Email<input type=email></label><button>Submit</button></form><a href="{other}">Apply now</a>')
    await agent.context.route('**/*', route)
    await agent.page.goto(board)
    adapter = adapter_for(board)
    assert await adapter.scope(agent.page.main_frame) is None
    await adapter.enter(agent.page.main_frame, {urlsplit(board).hostname, urlsplit(other).hostname}, lambda: True)
    assert agent.page.url == board
    assert await agent.page.get_by_role('textbox').input_value() == ''


async def test_recognized_host_without_tenant_cannot_enter_application(portal_browser):
    agent = portal_browser
    async def route(request_route):
        await request_route.fulfill(content_type='text/html', body='<a href="/Other/123">Apply now</a>')
    await agent.context.route('**/*', route)
    await agent.page.goto('https://jobs.ashbyhq.com/')
    await adapter_for(agent.page.url).enter(agent.page.main_frame, {'jobs.ashbyhq.com'}, lambda: True)
    assert agent.page.url == 'https://jobs.ashbyhq.com/'
