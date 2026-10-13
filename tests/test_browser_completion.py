"""Browser integration uses intercepted HTML only; no external accounts or requests."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from agent.browser import BrowserAgent
from agent.forms import fill_form
from agent.models import Config, Job, Profile, Resume, Search, Settings, Verification
from agent.verification import verify_destination, suspicious_text


@pytest_asyncio.fixture
async def fixture_browser(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        context.set_default_timeout(500)
        agent = BrowserAgent(tmp_path, headless=True)
        agent.context, agent.page = context, await context.new_page()
        # Keep real DOM, navigation and events; remove only production pacing.
        async def short_wait(milliseconds):
            await __import__('asyncio').sleep(0.01)
        agent.page.wait_for_timeout = short_wait
        yield agent
        await browser.close()


async def html_routes(agent, handler):
    async def route(request_route):
        body = handler(request_route.request.url)
        if body is None:
            await request_route.abort()
        else:
            await request_route.fulfill(content_type="text/html", body=body)
    await agent.context.route("**/*", route)


def cv_file(tmp_path):
    path = tmp_path / 'selected.pdf'
    path.write_bytes(b'fixture CV')
    return Resume(label='Selected', filename='selected.pdf', path=str(path), confirmed=True)


@pytest.mark.asyncio
async def test_discovery_paginates_lazy_cards_deduplicates_and_stops_repeated_page(fixture_browser):
    agent = fixture_browser
    searches = []
    def document(url):
        if '/jobs/search/' in url:
            start = int(parse_qs(urlsplit(url).query).get('start', ['0'])[0])
            searches.append(start)
            if start == 0:
                return '''<div class="jobs-search-results-list" style="height:20px;overflow:auto"><div style="height:100px"><a href="/jobs/view/101/">One</a></div></div>
                <script>document.querySelector('.jobs-search-results-list').onscroll=()=>{if(!document.querySelector('#lazy')){let a=document.createElement('a');a.id='lazy';a.href='/jobs/view/102/';a.innerText='Two';document.body.append(a)}};</script>'''
            return '<a href="/jobs/view/102/">Two</a><a href="/jobs/view/103/">Three</a>'
        if '/jobs/view/' in url:
            return '<h1>Engineer</h1><a class="topcard__org-name-link">Acme</a><div id="job-details">Python</div>'
        return None
    await html_routes(agent, document)
    jobs = await agent.discover(Search(), 10, max_pages=8)
    assert [j.linkedin_id for j in jobs] == ['101', '102', '103']
    assert searches == [0, 25, 50]


@pytest.mark.asyncio
async def test_discovery_respects_page_cap_and_cancel_guard(fixture_browser):
    agent = fixture_browser
    starts = []
    def document(url):
        if '/jobs/search/' in url:
            start = int(parse_qs(urlsplit(url).query).get('start', ['0'])[0]); starts.append(start)
            return f'<a href="/jobs/view/{100 + start}/">Engineer</a>'
        return '<h1>Engineer</h1><a class="topcard__org-name-link">Acme</a>'
    await html_routes(agent, document)
    jobs = await agent.discover(Search(titles=['Engineer', 'Developer']), 20, max_pages=2)
    assert [job.linkedin_id for job in jobs] == ['100']  # Same listing in both title queries deduplicates.
    assert starts == [0, 0]  # One bounded turn per title before the next page.
    from agent.forms import OperationPaused
    with pytest.raises(OperationPaused):
        await agent.discover(Search(), 20, lambda: False, max_pages=2)


@pytest.mark.asyncio
async def test_session_check_distinguishes_challenge_login_and_signed_in(fixture_browser):
    agent = fixture_browser
    pages = {'/feed/': '<nav><button class="global-nav__me">Me</button></nav>', '/login': '<input type="password">', '/checkpoint/challenge': '<p>Security verification</p>'}
    await html_routes(agent, lambda u: pages.get(urlsplit(u).path, ''))
    for path, want in [('/feed/', 'connected'), ('/login', 'needs_login'), ('/checkpoint/challenge', 'needs_input')]:
        await agent.page.goto('https://www.linkedin.com' + path)
        result = await agent.check_session()
        assert result['state'] == want
        assert result['details']


@pytest.mark.asyncio
async def test_custom_combobox_and_radio_widgets_select_exact_grounded_answer(fixture_browser):
    agent = fixture_browser
    await agent.page.set_content('''<form><div role="combobox" tabindex="0" aria-label="Country" aria-required="true" aria-controls="countries" onclick="document.querySelector('#countries').hidden=false">Select</div>
      <div role="listbox" id="countries" hidden><div role="option" onclick="document.querySelector('[role=combobox]').textContent='Canada';this.parentNode.hidden=true">Canada</div><div role="option">Germany</div></div>
      <fieldset><legend>Are you willing to relocate?</legend><div role="radio" aria-label="Yes" aria-checked="false" onclick="this.setAttribute('aria-checked','true')">Yes</div><div role="radio" aria-label="No" aria-checked="false">No</div></fieldset></form>''')
    outcome = await fill_form(agent.page.locator('form'), Profile(country='Canada', willing_to_relocate=True), Resume(label='CV'))
    assert await agent.page.get_by_role('combobox').inner_text() == 'Canada'
    assert await agent.page.get_by_role('radio', name='Yes', exact=True).get_attribute('aria-checked') == 'true'
    assert not outcome.missing
    assert outcome.sources['Country'] == 'profile:country'


@pytest.mark.asyncio
async def test_embedded_verified_greenhouse_dry_run_uploads_without_submit_or_guard(fixture_browser, tmp_path):
    agent = fixture_browser
    seen = []
    form = '''<form id="application_form" onsubmit="event.preventDefault();document.body.innerHTML='Application submitted successfully!'"><label>First name<input required></label><label>Email<input type="email" required></label><label>Resume<input type="file" required></label><button type="submit">Submit application</button></form>'''
    await html_routes(agent, lambda url: '<iframe src="https://boards.greenhouse.io/acme/jobs/123"></iframe>' if hostname_of(url) == 'careers.example' else form)
    async def verified(job, config):
        return Verification(state='eligible', allowed_hosts=['careers.example', 'boards.greenhouse.io'])
    agent.verify = verified
    job = Job(linkedin_id='1', title='Engineer', company='Acme', application_url='https://careers.example/apply')
    result = await agent.apply(job, Profile(first_name='Ada', email='ada@example.com'), cv_file(tmp_path), Config(), lambda: seen.append('submit') or True, dry_run=True)
    assert result.state == 'needs_input'
    assert 'ready for review' in result.reason.lower()
    assert seen == []
    frame = agent.page.frames[1]
    assert await frame.locator('input').first.input_value() == 'Ada'
    assert 'selected.pdf' in await frame.locator('[type=file]').input_value()
    assert job.adapter_name == 'greenhouse' and job.adapter_version
    assert await frame.get_by_role('button', name='Submit application').count() == 1


def hostname_of(url):
    return urlsplit(url).hostname


@pytest.mark.asyncio
async def test_unverified_frame_receives_no_applicant_data(fixture_browser, tmp_path):
    agent = fixture_browser
    await html_routes(agent, lambda url: '<iframe src="https://evil.example/collect"></iframe>' if hostname_of(url) == 'careers.example' else '<form><label>Email<input required></label></form>')
    async def verified(job, config):
        return Verification(state='eligible', allowed_hosts=['careers.example'])
    agent.verify = verified
    job = Job(linkedin_id='1', title='Engineer', company='Acme', application_url='https://careers.example/apply')
    result = await agent.apply(job, Profile(email='ada@example.com'), cv_file(tmp_path), Config(), lambda: True, dry_run=True)
    assert result.state == 'needs_input'
    assert await agent.page.frames[1].locator('input').input_value() == ''


@pytest.mark.asyncio
async def test_lever_adapter_enters_exact_host_application_link(fixture_browser, tmp_path):
    agent = fixture_browser
    def document(url):
        if urlsplit(url).path.endswith('/apply'):
            return '<form class="application-form"><label>Email<input required></label><label>Resume<input type=file required></label><button>Submit application</button></form>'
        return '<a class="postings-btn" href="/acme/123/apply">Apply for this job</a>'
    await html_routes(agent, document)
    async def verified(job, config):
        return Verification(state='eligible', allowed_hosts=['jobs.lever.co'])
    agent.verify = verified
    job = Job(linkedin_id='1', title='Engineer', company='Acme', application_url='https://jobs.lever.co/acme/123')
    result = await agent.apply(job, Profile(email='ada@example.com'), cv_file(tmp_path), Config(), lambda: True, dry_run=True)
    assert 'ready for review' in result.reason.lower()
    assert job.adapter_name == 'lever'
    assert agent.page.url == 'https://jobs.lever.co/acme/123/apply'


@pytest.mark.asyncio
@pytest.mark.parametrize('settings', [Settings(allow_registration=True), Settings(registration_hosts=['careers.example'])])
async def test_registration_requires_global_and_exact_host_opt_in(fixture_browser, tmp_path, settings, monkeypatch):
    agent = fixture_browser
    await html_routes(agent, lambda url: '<form><label>Email<input type=email required></label><label>Password<input type=password required></label><button>Create account</button></form>')
    monkeypatch.setattr('agent.browser.keyring.get_password', lambda *a: None)
    async def verified(job, config):
        return Verification(state='eligible', allowed_hosts=['careers.example'])
    agent.verify = verified
    job = Job(linkedin_id='1', title='Engineer', company='Acme', application_url='https://careers.example/signup')
    result = await agent.apply(job, Profile(email='ada@example.com'), cv_file(tmp_path), Config(settings=settings), lambda: True)
    assert result.state == 'needs_input'
    assert 'registration' in result.reason.lower()
    assert await agent.page.locator('[type=password]').input_value() == ''


@pytest.mark.asyncio
async def test_registration_saves_strong_password_before_any_sensitive_fill(fixture_browser, tmp_path, monkeypatch):
    agent = fixture_browser
    stored = {}
    events = []
    await agent.page.expose_function('record', lambda value: events.append(value))
    await html_routes(agent, lambda u: '''<form onsubmit="event.preventDefault();document.body.innerHTML='Enter your verification code'"><label>Email<input type=email required oninput="window.record('filled')"></label><label>Password<input type=password required></label><button>Create account</button></form>''')
    monkeypatch.setattr('agent.browser.keyring.get_password', lambda *a: None)
    def save(service, host, value):
        events.append('saved'); stored[host] = json.loads(value)
    monkeypatch.setattr('keyring.set_password', save)
    async def verified(job, config):
        return Verification(state='eligible', allowed_hosts=['careers.example'])
    agent.verify = verified
    job = Job(linkedin_id='1', title='Engineer', company='Acme', application_url='https://careers.example/signup')
    config = Config(settings=Settings(allow_registration=True, registration_hosts=['careers.example']))
    result = await agent.apply(job, Profile(email='ada@example.com'), cv_file(tmp_path), config, lambda: True)
    assert result.state == 'needs_input'
    assert 'verification' in result.reason.lower()
    assert events[:2] == ['saved', 'filled']
    password = stored['careers.example']['password']
    assert len(password) >= 24 and any(c.isupper() for c in password) and any(c.islower() for c in password) and any(c.isdigit() for c in password)


@pytest.mark.asyncio
async def test_registration_never_accepts_attestation_or_creates_account_in_dry_run(fixture_browser, tmp_path, monkeypatch):
    agent = fixture_browser
    await html_routes(agent, lambda u: '<form><label>Email<input type=email required></label><label>Password<input type=password required></label><label>I certify all information is accurate<input type=checkbox required></label><button>Create account</button></form>')
    monkeypatch.setattr('agent.browser.keyring.get_password', lambda *a: None)
    def forbidden(*args):
        raise AssertionError('must not create credentials for an unsupported registration')
    monkeypatch.setattr('keyring.set_password', forbidden)
    async def verified(job, config):
        return Verification(state='eligible', allowed_hosts=['careers.example'])
    agent.verify = verified
    job = Job(linkedin_id='1', title='Engineer', company='Acme', application_url='https://careers.example/signup')
    config = Config(settings=Settings(allow_registration=True, registration_hosts=['careers.example']))
    for dry_run in [False, True]:
        result = await agent.apply(job, Profile(email='ada@example.com'), cv_file(tmp_path), config, lambda: True, dry_run=dry_run)
        assert result.state == 'needs_input'
        assert not await agent.page.locator('[type=checkbox]').is_checked()
        assert await agent.page.locator('[type=password]').input_value() == ''


@pytest.mark.asyncio
async def test_sso_hosts_are_added_only_for_exact_verified_portal():
    job = Job(linkedin_id='1', title='Engineer', company='Acme', application_url='https://jobs.lever.co/acme/123', company_website='https://acme.example')
    async def official(url):
        return '<a href="https://jobs.lever.co/acme/123">Engineer</a>'
    settings = Settings(sso_hosts={'jobs.lever.co': ['login.example'], 'other.example': ['unrelated.example']})
    result = await verify_destination(job, settings, fetch=official)
    assert result.state == 'eligible'
    assert 'login.example' in result.allowed_hosts
    assert 'unrelated.example' not in result.allowed_hosts


@pytest.mark.parametrize('text', ['Wire a deposit to secure the job', 'Upload your passport before an interview', 'Guaranteed employment after purchasing our training', 'Send your bank account details to the recruiter'])
def test_broader_employer_risks_block_unreviewed_solicitations(text):
    assert suspicious_text(text)


def test_employer_safety_negations_do_not_trigger_scam_warning():
    assert not suspicious_text('We never ask for application fees or passport uploads. We do not guarantee employment.')


@pytest.mark.asyncio
async def test_registration_dry_run_never_saves_credentials_or_clicks_create(fixture_browser, tmp_path, monkeypatch):
    agent = fixture_browser
    await html_routes(agent, lambda u: '<form onsubmit="event.preventDefault();document.body.innerHTML=\'Account created\'"><label>Email<input type=email required></label><label>Password<input type=password required></label><button>Create account</button></form>')
    def forbidden(*args):
        raise AssertionError('dry run must never access new account credentials')
    monkeypatch.setattr('keyring.set_password', forbidden)
    async def verified(job, config):
        return Verification(state='eligible', allowed_hosts=['careers.example'])
    agent.verify = verified
    job = Job(linkedin_id='1', title='Engineer', company='Acme', application_url='https://careers.example/signup')
    result = await agent.apply(job, Profile(email='ada@example.com'), cv_file(tmp_path), Config(settings=Settings(allow_registration=True, registration_hosts=['careers.example'])), forbidden, dry_run=True)
    assert result.state == 'needs_input'
    assert await agent.page.get_by_role('button', name='Create account').count() == 1
    assert await agent.page.locator('[type=password]').input_value() == ''


@pytest.mark.asyncio
async def test_login_credentials_never_fill_form_posting_to_another_approved_sso_host(fixture_browser, monkeypatch):
    agent = fixture_browser
    filled = []
    await agent.page.expose_function('recordCredentialFill', lambda: filled.append(True))
    await html_routes(agent, lambda u: '<form action="https://login.example/collect"><label>Email<input type=email></label><input type=password oninput="window.recordCredentialFill()"><button>Sign in</button></form>')
    await agent.page.goto('https://careers.example/login')
    agent.sensitive_hosts = {'careers.example', 'login.example'}
    monkeypatch.setattr('keyring.get_password', lambda service, host: json.dumps({'username':'ada@example.com','password':'private'}))
    assert not await agent._login()
    assert filled == []
    assert await agent.page.locator('[type=password]').input_value() == ''


@pytest.mark.asyncio
async def test_login_narrows_network_to_exact_credentials_host(fixture_browser, monkeypatch):
    agent = fixture_browser
    seen = []
    await agent.page.expose_function('recordBoundary', lambda: seen.append(set(agent.sensitive_hosts)))
    await html_routes(agent, lambda u: 'Signed in' if urlsplit(u).path == '/session' else '<form action="/session" method=post><input type=email><input type=password oninput="window.recordBoundary()"><button>Sign in</button></form>')
    await agent.page.goto('https://careers.example/login')
    agent.sensitive_hosts = {'careers.example', 'login.example'}
    monkeypatch.setattr('keyring.get_password', lambda service, host: json.dumps({'username':'ada@example.com','password':'private'}))
    assert await agent._login()
    assert seen == [{'careers.example'}]
    assert agent.sensitive_hosts == {'careers.example', 'login.example'}
    assert agent.credential_host is None


@pytest.mark.asyncio
async def test_submission_confirmation_is_read_inside_verified_iframe(fixture_browser, tmp_path):
    agent = fixture_browser
    await html_routes(agent, lambda u: '<form><label>Newsletter email<input type=email required></label></form><iframe src="https://jobs.lever.co/acme/123/apply"></iframe>' if hostname_of(u) == 'careers.example' else '<form onsubmit="event.preventDefault();document.body.innerHTML=\'Application submitted successfully!\'"><label>Email<input required></label><label>Resume<input type=file required></label><button>Submit application</button></form>')
    async def verified(job, config):
        return Verification(state='eligible', allowed_hosts=['careers.example','jobs.lever.co'])
    agent.verify = verified
    job = Job(linkedin_id='1', title='Engineer', company='Acme', application_url='https://careers.example/apply')
    submissions = []
    result = await agent.apply(job, Profile(email='ada@example.com'), cv_file(tmp_path), Config(), lambda: submissions.append(True) or True)
    assert result.state == 'submitted'
    assert submissions == [True]


@pytest.mark.asyncio
async def test_official_application_page_corroborates_supported_embedded_portal_only():
    job = Job(linkedin_id='1', title='Engineer', company='Acme', application_url='https://acme.example/apply', company_website='https://acme.example')
    async def official(url):
        if url == 'https://acme.example':
            return '<a href="/apply">Engineer</a>'
        return '<iframe src="https://boards.greenhouse.io/acme/jobs/123"></iframe><iframe src="https://untrusted.example/form"></iframe>'
    result = await verify_destination(job, Settings(), fetch=official)
    assert result.state == 'eligible'
    assert 'boards.greenhouse.io' in result.allowed_hosts
    assert 'untrusted.example' not in result.allowed_hosts


@pytest.mark.asyncio
async def test_visible_poster_and_verification_context_are_recorded_without_establishing_trust(fixture_browser):
    agent = fixture_browser
    await html_routes(agent, lambda u: '''<h1>Engineer</h1><a class="topcard__org-name-link">Acme</a><div id="job-details">Upload your passport before an interview</div><div class="hirer-card__hirer-information"><a href="/in/recruiter">Recruiter Name</a><span class="hirer-card__hirer-information-description">Recruiter at Acme</span><span aria-label="Verified recruiter">Verified</span></div>''')
    job = await agent._read_job('123', Search())
    assert job.poster_name == 'Recruiter Name'
    assert job.poster_url == 'https://www.linkedin.com/in/recruiter'
    assert job.verified_poster
    assert job.risk_flags
    assert (await verify_destination(job, Settings())).state == 'blocked'


def test_unrelated_no_experience_statement_does_not_hide_financial_solicitation():
    assert suspicious_text('No experience required, wire a deposit to secure the job')


@pytest.mark.asyncio
async def test_ambiguous_registration_attempt_is_not_repeated(fixture_browser, monkeypatch):
    from agent.registration import register_account, registration_form
    agent = fixture_browser
    await html_routes(agent, lambda u: '<form onsubmit="event.preventDefault();window.attempts=(window.attempts||0)+1"><label>Email<input type=email required></label><label>Password<input type=password required></label><button>Create account</button></form>')
    await agent.page.goto('https://careers.example/signup')
    stored = {}
    monkeypatch.setattr('keyring.get_password', lambda service, host: stored.get(host))
    monkeypatch.setattr('keyring.set_password', lambda service, host, value: stored.__setitem__(host,value))
    settings = Settings(allow_registration=True, registration_hosts=['careers.example'])
    form = await registration_form(agent.page.main_frame)
    first = await register_account(agent.page.main_frame, form, Profile(email='ada@example.com'), settings, lambda: True)
    second = await register_account(agent.page.main_frame, form, Profile(email='ada@example.com'), settings, lambda: True)
    assert first.state == second.state == 'needs_input'
    assert await agent.page.evaluate('window.attempts') == 1
    assert json.loads(stored['careers.example'])['registration_pending'] is True


@pytest.mark.asyncio
async def test_existing_user_credentials_are_not_used_for_new_registration(fixture_browser, monkeypatch):
    from agent.registration import register_account, registration_form
    agent = fixture_browser
    await html_routes(agent, lambda u: '<form onsubmit="event.preventDefault();window.attempts=1"><label>Email<input type=email required></label><label>Password<input type=password required></label><button>Create account</button></form>')
    await agent.page.goto('https://careers.example/signup')
    monkeypatch.setattr('keyring.get_password', lambda *a: json.dumps({'username':'ada@example.com','password':'existing-account'}))
    result = await register_account(agent.page.main_frame, await registration_form(agent.page.main_frame), Profile(email='ada@example.com'), Settings(allow_registration=True, registration_hosts=['careers.example']), lambda: True)
    assert result.state == 'needs_input'
    assert await agent.page.evaluate('window.attempts || 0') == 0
    assert await agent.page.locator('[type=password]').input_value() == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('page_url,action', [('http://careers.example/signup','http://careers.example/create'), ('https://careers.example/signup','http://careers.example/create')])
async def test_signup_credentials_require_https_page_and_action(fixture_browser, monkeypatch, page_url, action):
    from agent.registration import register_account, registration_form
    agent = fixture_browser
    await html_routes(agent, lambda u: f'<form action="{action}"><label>Email<input type=email required></label><label>Password<input type=password required></label><button>Create account</button></form>')
    await agent.page.goto(page_url)
    accessed = []
    def forbidden(*args):
        accessed.append(True)
        return None
    monkeypatch.setattr('keyring.get_password', forbidden)
    monkeypatch.setattr('keyring.set_password', forbidden)
    result = await register_account(agent.page.main_frame, await registration_form(agent.page.main_frame), Profile(email='ada@example.com'), Settings(allow_registration=True, registration_hosts=['careers.example']), lambda: True)
    assert result.state == 'needs_input'
    assert await agent.page.locator('[type=password]').input_value() == ''
    assert accessed == []
    assert 'https' in result.reason.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize('page_url,action', [('http://careers.example/login','http://careers.example/session'), ('https://careers.example/login','http://careers.example/session')])
async def test_login_credentials_require_https_page_and_action(fixture_browser, monkeypatch, page_url, action):
    agent = fixture_browser
    entered = []
    await agent.page.expose_function('record', lambda: entered.append(True))
    await html_routes(agent, lambda u: f'<form action="{action}"><input type=email><input type=password oninput="window.record()"><button>Sign in</button></form>')
    await agent.page.goto(page_url)
    agent.sensitive_hosts = {'careers.example'}
    monkeypatch.setattr('keyring.get_password', lambda *a: json.dumps({'username':'ada@example.com','password':'private'}))
    assert not await agent._login()
    assert entered == []


@pytest.mark.asyncio
async def test_credential_phase_blocks_same_host_http_downgrade(tmp_path, monkeypatch):
    agent = BrowserAgent(tmp_path)
    agent.sensitive_hosts = {'careers.example'}
    agent.credential_host = 'careers.example'
    monkeypatch.setattr('agent.browser.public_url', lambda url: True)
    class Request:
        url = 'http://careers.example/leak?password=secret'
        method = 'GET'
        def is_navigation_request(self): return True
    class Route:
        request = Request()
        result = ''
        fetched = False
        async def fetch(self, **kwargs):
            self.fetched = True
            class Response:
                status = 200
                async def dispose(self): pass
            return Response()
        async def fulfill(self, **kwargs): self.result = 'released'
        async def abort(self): self.result = 'blocked'
    route = Route()
    await agent._route(route)
    assert not route.fetched
    assert route.result == 'blocked'


@pytest.mark.asyncio
async def test_public_linkedin_job_with_recruiter_link_is_not_a_signed_in_session(fixture_browser):
    agent = fixture_browser
    await html_routes(agent, lambda u: '<h1>Engineer</h1><a href="/in/recruiter">Meet the recruiter</a>')
    await agent.page.goto('https://www.linkedin.com/jobs/view/123/')
    assert (await agent.check_session())['state'] != 'connected'


@pytest.mark.asyncio
@pytest.mark.parametrize('redirect_status,expect_callback', [(302, True), (307, False)])
@pytest.mark.parametrize('retained_popup', [False, True])
@pytest.mark.parametrize('iframe_login', [False, True])
async def test_login_redirect_restores_approved_sso_only_after_document_destroyed(fixture_browser, monkeypatch, redirect_status, expect_callback, retained_popup, iframe_login):
    agent = fixture_browser
    released = []
    callbacks = []
    monkeypatch.setattr('agent.browser.public_url', lambda url, **kwargs: True)
    monkeypatch.setattr('keyring.get_password', lambda *a: json.dumps({'username':'ada@example.com','password':'private'}))
    class Response:
        def __init__(self, status, body='', headers=None):
            self.status, self.body, self.headers = status, body, headers or {}
        async def dispose(self): pass
    async def route(real_route):
        class Transport:
            request = real_route.request
            async def fetch(self, **kwargs):
                request = self.request
                released.append((request.url, request.method, request.post_data))
                if request.method == 'POST':
                    return Response(redirect_status, headers={'location':'https://login.example/callback'})
                if hostname_of(request.url) == 'login.example':
                    callbacks.append((request.method, request.post_data, agent.page.url))
                    return Response(200, '<div class="global-nav__me">Signed in</div>')
                if request.url.endswith('/embedded'):
                    return Response(200, '<script>window.retainedPassword = "private"</script><iframe src="/login"></iframe>')
                return Response(200, '<form action="/session" method="post"><input type=email name=username><input type=password name=password><button>Sign in</button></form>')
            async def abort(self): await real_route.abort()
            async def fulfill(self, **kwargs):
                response = kwargs.pop('response', None)
                if response:
                    kwargs.update(status=response.status, body=response.body, content_type='text/html')
                await real_route.fulfill(**kwargs)
        await agent._route(Transport())
    await agent.context.route('**/*', route)
    agent.sensitive_hosts = {'careers.example','login.example'}
    await agent.page.goto('https://careers.example/embedded' if iframe_login else 'https://careers.example/login')
    login_frame = agent.page.frames[1] if iframe_login else agent.page.main_frame
    popup = None
    if retained_popup:
        popup = await agent.context.new_page()
        await popup.goto('https://careers.example/retained')
        await popup.evaluate("window.retainedPassword = 'private'")
    await agent._login(frame=login_frame)
    if expect_callback and iframe_login:
        assert login_frame.is_detached(), 'Parent and sibling credential documents must be destroyed'
        assert await agent.page.evaluate('window.retainedPassword') is None
    if expect_callback and popup is not None:
        assert popup.is_closed(), 'Credential documents must be destroyed before hosts reopen'
    assert bool(callbacks) is expect_callback
    if expect_callback:
        method, body, previous_url = callbacks[0]
        assert method == 'GET' and body is None
        assert previous_url == 'about:blank'
        assert agent.sensitive_hosts == {'careers.example','login.example'}
        assert agent.credential_host is None
    assert all(hostname_of(url) == 'careers.example' for url, method, body in released if method == 'POST')
