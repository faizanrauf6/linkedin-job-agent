"""Offline browser regressions for review steps and retained application state."""
import asyncio
import hashlib
import json

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from agent.browser import BrowserAgent
from agent.forms import fill_form
from agent.models import Config, Job, Profile, Resume, Verification


@pytest_asyncio.fixture
async def browser_agent(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        context.set_default_timeout(700)
        agent = BrowserAgent(tmp_path, headless=True)
        agent.context, agent.page = context, await context.new_page()
        async def pace(_):
            await asyncio.sleep(.01)
        agent.page.wait_for_timeout = pace
        async def verify(*_):
            return Verification(state='eligible', allowed_hosts=['careers.example', 'www.linkedin.com', 'jobs.lever.co'])
        agent.verify = verify
        yield agent
        await browser.close()


def resume_file(tmp_path):
    content = b'%PDF-1.4 selected original fixture'
    path = tmp_path / 'selected.pdf'
    path.write_bytes(content)
    return Resume(label='Selected', filename='selected.pdf', path=str(path), confirmed=True,
                  sha256=hashlib.sha256(content).hexdigest())


def opportunity(easy=False):
    return Job(linkedin_id='123', title='Engineer', company='Acme', easy_apply=easy,
               url='https://www.linkedin.com/jobs/view/123/', application_url='https://careers.example/apply')


async def route_html(agent, body):
    await agent.context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=body))


@pytest.mark.asyncio
@pytest.mark.parametrize('easy', [False, True])
async def test_review_without_inputs_finishes_reserved_multistep_application(browser_agent, tmp_path, easy):
    agent = browser_agent
    review = '<p>Review your application</p><button onclick="document.body.innerHTML=\'Application submitted successfully!\'">Submit application</button>'
    form = '<label>Email<input required></label><label>Resume<input type=file required></label><button type=button id=next>Next</button>'
    body = f'<div role=dialog>{form}</div>' if easy else f'<form>{form}</form>'
    if easy:
        body = '<button onclick="document.querySelector(\'[role=dialog]\').hidden=false">Easy Apply</button>' + body.replace('role=dialog', 'role=dialog hidden')
    body += '<script>document.querySelector("#next").onclick=()=>{document.querySelector("form,[role=dialog]").innerHTML=' + json.dumps(review) + '}</script>'
    await route_html(agent, body)
    reservations = []
    result = await agent.apply(opportunity(easy), Profile(email='ada@example.com'), resume_file(tmp_path), Config(), lambda: reservations.append(True) or True)
    assert result.state == 'submitted'
    assert reservations == [True]


@pytest.mark.asyncio
async def test_matching_prefilled_fact_is_preserved_without_input_events(browser_agent):
    page = browser_agent.page
    await page.set_content('<form><label>Email<input type=email required value="ada@example.com" oninput="window.edits=(window.edits||0)+1"></label></form>')
    outcome = await fill_form(page.locator('form'), Profile(email='ada@example.com'), Resume(label='CV'))
    assert not outcome.missing
    assert outcome.answers['Email'] == 'ada@example.com'
    assert await page.evaluate('window.edits || 0') == 0


@pytest.mark.asyncio
async def test_unknown_prefilled_value_requires_confirmation_then_continues(browser_agent, tmp_path):
    agent = browser_agent
    await route_html(agent, '<form onsubmit="event.preventDefault();document.body.innerHTML=\'Application submitted successfully!\'"><label>Favorite color<input required></label><label>Resume<input type=file required></label><button>Submit application</button></form>')
    job, cv, config = opportunity(), resume_file(tmp_path), Config()
    first = await agent.apply(job, Profile(), cv, config, lambda: pytest.fail('unconfirmed submission'))
    assert first.state == 'needs_input'
    await agent.page.locator('input:not([type=file])').fill('blue')
    snapshot = await agent.get_continuation(job, config, cv)
    assert snapshot['fields'][0]['value'] == 'blue'
    await agent.confirm_continuation(job, config, cv, snapshot['token'])
    reserved = []
    final = await agent.apply(job, Profile(), cv, config, lambda: reserved.append(True) or True)
    assert final.state == 'submitted'
    assert final.answers['Favorite color'] == 'blue'
    assert final.sources['Favorite color'] == 'user_confirmed_current_field'
    assert reserved == [True]


@pytest.mark.asyncio
async def test_confirmation_rejects_changed_value_or_document(browser_agent, tmp_path):
    agent = browser_agent
    await route_html(agent, '<form><label>Favorite color<input required value=blue></label><label>Resume<input type=file required></label><button>Submit application</button></form>')
    job, cv, config = opportunity(), resume_file(tmp_path), Config()
    await agent.apply(job, Profile(), cv, config, lambda: False)
    snapshot = await agent.get_continuation(job, config, cv)
    await agent.page.locator('input:not([type=file])').fill('red')
    with pytest.raises(ValueError):
        await agent.confirm_continuation(job, config, cv, snapshot['token'])
    snapshot = await agent.get_continuation(job, config, cv)
    await agent.confirm_continuation(job, config, cv, snapshot['token'])
    await agent.page.locator('input:not([type=file])').fill('green')
    outcome = await agent.apply(job, Profile(), cv, config, lambda: pytest.fail('changed answer submitted'))
    assert outcome.state == 'needs_input'
    snapshot = await agent.get_continuation(job, config, cv)
    await agent.page.reload()
    with pytest.raises(ValueError):
        await agent.confirm_continuation(job, config, cv, snapshot['token'])


@pytest.mark.asyncio
async def test_personal_attestations_cannot_be_approved_as_prefilled(browser_agent, tmp_path):
    agent = browser_agent
    await route_html(agent, '<form><label>I certify all information is accurate<input required value=yes></label><label>Resume<input type=file required></label><button>Submit application</button></form>')
    job, cv, config = opportunity(), resume_file(tmp_path), Config()
    await agent.apply(job, Profile(), cv, config, lambda: pytest.fail('attestation submitted'))
    snapshot = await agent.get_continuation(job, config, cv)
    assert snapshot['fields'] == []


@pytest.mark.asyncio
async def test_continuation_replaces_changed_upload_with_selected_original(browser_agent, tmp_path):
    agent = browser_agent
    await route_html(agent, '<form onsubmit="event.preventDefault();window.sentFile=this.querySelector(\'[type=file]\').files[0].name;document.body.innerHTML=\'Application submitted successfully!\'"><label>Favorite color<input required></label><label>Resume<input type=file required></label><button>Submit application</button></form>')
    job, cv, config = opportunity(), resume_file(tmp_path), Config()
    await agent.apply(job, Profile(), cv, config, lambda: False)
    wrong = tmp_path / 'wrong.pdf'; wrong.write_bytes(b'wrong CV')
    await agent.page.locator('[type=file]').set_input_files(str(wrong))
    await agent.page.locator('input:not([type=file])').fill('blue')
    snapshot = await agent.get_continuation(job, config, cv)
    await agent.confirm_continuation(job, config, cv, snapshot['token'])
    result = await agent.apply(job, Profile(), cv, config, lambda: True)
    assert result.state == 'submitted'
    assert await agent.page.evaluate('window.sentFile') == 'selected.pdf'


@pytest.mark.asyncio
async def test_unavailable_detail_does_not_starve_remaining_discovery_candidates(browser_agent):
    from agent.models import Search
    agent = browser_agent
    async def route(r):
        if '/jobs/search/' in r.request.url:
            body = '<a href="/jobs/view/101/">Expired</a><a href="/jobs/view/102/">Valid</a>'
        elif '/101/' in r.request.url:
            body = '<h1>Listing no longer available</h1>'
        else:
            body = '<h1>Engineer</h1><a class="topcard__org-name-link">Acme</a>'
        await r.fulfill(content_type='text/html', body=body)
    await agent.context.route('**/*', route)
    progress = {}
    assert await agent.discover(Search(), 1, max_pages=1, progress=progress) == []
    second = await agent.discover(Search(), 1, max_pages=1, progress=progress)
    assert [job.linkedin_id for job in second] == ['102']


@pytest.mark.asyncio
async def test_normal_login_destroys_secret_documents_then_restores_verified_hosts(browser_agent, monkeypatch):
    agent = browser_agent
    signed_in = False
    async def route(r):
        nonlocal signed_in
        if r.request.method == 'POST':
            signed_in = True
        if 'jobs.lever.co' in r.request.url:
            body = '<form><label>Email<input></label></form>'
        elif signed_in:
            body = '<iframe src="https://jobs.lever.co/acme/123"></iframe>'
        else:
            body = '<script>window.retainedSecret="private"</script><form action="/session" method=post><input type=email name=username><input type=password name=password><button>Sign in</button></form>'
        await r.fulfill(content_type='text/html', body=body)
    await agent.context.route('**/*', route)
    await agent.page.goto('https://careers.example/login')
    popup = await agent.context.new_page()
    await popup.goto('https://careers.example/retained')
    agent.sensitive_hosts = {'careers.example', 'jobs.lever.co'}
    monkeypatch.setattr('keyring.get_password', lambda *_: json.dumps({'username':'ada@example.com','password':'private'}))
    assert await agent._login()
    assert popup.is_closed()
    assert agent.sensitive_hosts == {'careers.example', 'jobs.lever.co'}
    assert agent.credential_host is None
    assert await agent.page.evaluate('window.retainedSecret') is None
    await agent.page.frame_locator('iframe').locator('input').wait_for()


@pytest.mark.asyncio
@pytest.mark.parametrize('matching', [True, False])
async def test_saved_linkedin_cv_requires_matching_content(browser_agent, tmp_path, matching):
    agent, cv = browser_agent, resume_file(tmp_path)
    async def route(r):
        if r.request.url.endswith('/document/selected.pdf'):
            await r.fulfill(content_type='application/pdf', body=open(cv.path, 'rb').read() if matching else b'different document')
        else:
            await r.fulfill(content_type='text/html', body='''<button onclick="document.querySelector('[role=dialog]').hidden=false">Easy Apply</button><div role=dialog hidden><div class="jobs-document-upload-redesign-card"><label>selected.pdf<input type=radio name=resume></label><a href="/document/selected.pdf" download>Download resume</a></div><button onclick="document.body.innerHTML='Application submitted successfully!'">Submit application</button></div>''')
    await agent.context.route('**/*', route)
    reservations = []
    result = await agent.apply(opportunity(True), Profile(), cv, Config(), lambda: reservations.append(True) or True)
    assert result.state == ('submitted' if matching else 'needs_input')
    assert reservations == ([True] if matching else [])


@pytest.mark.asyncio
async def test_continuation_rechecks_saved_document_bytes(browser_agent, tmp_path):
    agent, cv = browser_agent, resume_file(tmp_path)
    content = open(cv.path, 'rb').read()
    async def route(r):
        if '/document/' in r.request.url:
            await r.fulfill(content_type='application/pdf', body=content)
        else:
            await r.fulfill(content_type='text/html', body='''<button onclick="document.querySelector('[role=dialog]').hidden=false">Easy Apply</button><div role=dialog hidden><div data-resume-id=selected><label>selected.pdf<input type=radio name=resume></label><a href="/document/selected.pdf">Download resume</a></div><button onclick="document.body.innerHTML='Application submitted successfully!'">Submit application</button></div>''')
    await agent.context.route('**/*', route)
    job, config = opportunity(True), Config()
    first = await agent.apply(job, Profile(), cv, config, lambda: False, dry_run=True)
    assert first.state == 'needs_input'
    content = b'different CV under the same URL'
    result = await agent.apply(job, Profile(), cv, config, lambda: pytest.fail('changed saved document submitted'))
    assert result.state == 'needs_input'


@pytest.mark.asyncio
async def test_replaced_upload_control_does_not_reuse_detached_file_proof(browser_agent, tmp_path):
    agent = browser_agent
    await route_html(agent, '<form onsubmit="event.preventDefault();window.sentFile=this.querySelector(\'[type=file]\').files[0].name;document.body.innerHTML=\'Application submitted successfully!\'"><label>Resume<input type=file required></label><button>Submit application</button></form>')
    job, cv, config = opportunity(), resume_file(tmp_path), Config()
    await agent.apply(job, Profile(), cv, config, lambda: False, dry_run=True)
    await agent.page.locator('[type=file]').evaluate('el => el.replaceWith(el.cloneNode())')
    wrong = tmp_path / 'wrong.pdf'; wrong.write_bytes(b'wrong CV')
    await agent.page.locator('[type=file]').set_input_files(str(wrong))
    result = await agent.apply(job, Profile(), cv, config, lambda: True)
    assert result.state == 'submitted'
    assert await agent.page.evaluate('window.sentFile') == 'selected.pdf'


@pytest.mark.asyncio
async def test_confirmation_does_not_survive_changed_question_on_same_input(browser_agent, tmp_path):
    agent = browser_agent
    await route_html(agent, '<form><label><span>Favorite color</span><input required value=blue></label><label>Resume<input type=file required></label><button>Submit application</button></form>')
    job, cv, config = opportunity(), resume_file(tmp_path), Config()
    await agent.apply(job, Profile(), cv, config, lambda: False)
    snapshot = await agent.get_continuation(job, config, cv)
    await agent.confirm_continuation(job, config, cv, snapshot['token'])
    await agent.page.locator('span').evaluate("el => el.textContent = 'Preferred office'")
    result = await agent.apply(job, Profile(), cv, config, lambda: pytest.fail('stale question approval submitted'))
    assert result.state == 'needs_input'
    assert 'Preferred office' in result.questions


@pytest.mark.asyncio
async def test_manually_removed_upload_is_not_a_review_step_transfer(browser_agent, tmp_path):
    agent = browser_agent
    await route_html(agent, '<form><label id=cv>Resume<input type=file required></label><button>Submit application</button></form>')
    job, cv, config = opportunity(), resume_file(tmp_path), Config()
    await agent.apply(job, Profile(), cv, config, lambda: False, dry_run=True)
    await agent.page.locator('#cv').evaluate('el => el.remove()')
    result = await agent.apply(job, Profile(), cv, config, lambda: pytest.fail('removed attachment submitted'))
    assert result.state == 'needs_input'


@pytest.mark.asyncio
async def test_removed_saved_card_requires_replacement_upload(browser_agent, tmp_path):
    agent, cv = browser_agent, resume_file(tmp_path)
    async def route(r):
        if '/document/' in r.request.url:
            await r.fulfill(content_type='application/pdf', body=open(cv.path, 'rb').read())
        else:
            await r.fulfill(content_type='text/html', body='''<button onclick="document.querySelector('[role=dialog]').hidden=false">Easy Apply</button><div role=dialog hidden><div data-resume-id=selected><label>selected.pdf<input type=radio name=resume></label><a href="/document/selected.pdf">Download resume</a></div><label>Resume<input type=file></label><button onclick="window.sentFile=document.querySelector('[type=file]').files[0]?.name;document.body.innerHTML='Application submitted successfully!'">Submit application</button></div>''')
    await agent.context.route('**/*', route)
    job, config = opportunity(True), Config()
    await agent.apply(job, Profile(), cv, config, lambda: False, dry_run=True)
    assert await agent.page.locator('[type=file]').input_value() == ''
    await agent.page.locator('[data-resume-id]').evaluate('el => el.remove()')
    result = await agent.apply(job, Profile(), cv, config, lambda: True)
    assert result.state == 'submitted'
    assert await agent.page.evaluate('window.sentFile') == 'selected.pdf'


@pytest.mark.asyncio
async def test_next_does_not_authorize_later_manual_attachment_removal(browser_agent, tmp_path):
    agent = browser_agent
    await route_html(agent, '''<form><label id=cv>Resume<input type=file required></label><div id=question hidden><label>Favorite color<input required></label></div><button type=button onclick="document.querySelector('#question').hidden=false;this.textContent='Submit application';this.onclick=()=>document.body.innerHTML='Application submitted successfully!'">Next</button></form>''')
    job, cv, config = opportunity(), resume_file(tmp_path), Config()
    first = await agent.apply(job, Profile(), cv, config, lambda: False)
    assert first.state == 'needs_input'
    assert first.questions == ['Favorite color']
    await agent.page.locator('#cv').evaluate('el => el.remove()')
    await agent.page.locator('#question input').fill('blue')
    snapshot = await agent.get_continuation(job, config, cv)
    await agent.confirm_continuation(job, config, cv, snapshot['token'])
    result = await agent.apply(job, Profile(), cv, config, lambda: pytest.fail('attachment removed after Next was submitted'))
    assert result.state == 'needs_input'


@pytest.mark.asyncio
async def test_next_does_not_authorize_later_saved_card_removal(browser_agent, tmp_path):
    agent, cv = browser_agent, resume_file(tmp_path)
    async def route(r):
        if '/document/' in r.request.url:
            await r.fulfill(content_type='application/pdf', body=open(cv.path, 'rb').read())
        else:
            await r.fulfill(content_type='text/html', body='''<button onclick="document.querySelector('[role=dialog]').hidden=false">Easy Apply</button><div role=dialog hidden><div data-resume-id=selected><label>selected.pdf<input type=radio name=resume></label><a href="/document/selected.pdf">Download resume</a></div><div id=question hidden><label>Favorite color<input required></label></div><button onclick="document.querySelector('#question').hidden=false;this.textContent='Submit application';this.onclick=()=>document.body.innerHTML='Application submitted successfully!'">Next</button></div>''')
    await agent.context.route('**/*', route)
    job, config = opportunity(True), Config()
    first = await agent.apply(job, Profile(), cv, config, lambda: False)
    assert first.questions == ['Favorite color']
    await agent.page.locator('[data-resume-id]').evaluate('el => el.remove()')
    await agent.page.locator('#question input').fill('blue')
    snapshot = await agent.get_continuation(job, config, cv)
    await agent.confirm_continuation(job, config, cv, snapshot['token'])
    result = await agent.apply(job, Profile(), cv, config, lambda: pytest.fail('removed saved attachment submitted'))
    assert result.state == 'needs_input'
