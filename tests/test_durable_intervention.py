import pytest

from agent.models import Config, Profile
from agent.store import Store
from test_application_reliability import browser_agent, opportunity, resume_file, route_html


@pytest.mark.asyncio
async def test_confirmed_missing_answer_survives_browser_document_loss(browser_agent, tmp_path):
    agent = browser_agent
    store = agent.checkpoint_store = Store(tmp_path / 'checkpoints.db')
    job, cv, config = store.add_job(opportunity()), resume_file(tmp_path), Config()
    await route_html(agent, '<form onsubmit="event.preventDefault();document.body.innerHTML=\'Application submitted successfully!\'"><label>Favorite color<input required></label><label>Resume<input type=file required></label><button>Submit application</button></form>')
    first = await agent.apply(job, Profile(), cv, config, lambda: False)
    assert first.state == 'needs_input'
    await agent.page.locator('input:not([type=file])').fill('blue')
    snapshot = await agent.get_continuation(job, config, cv)
    await agent.confirm_continuation(job, config, cv, snapshot['token'])
    agent.pending_application = None
    await agent.page.goto('about:blank')
    result = await agent.apply(job, Profile(), cv, config, lambda: True)
    assert result.state == 'submitted'
    assert result.answers['Favorite color'] == 'blue'
    assert store.checkpoint(job.id) is None


@pytest.mark.asyncio
async def test_missing_field_details_include_current_options_and_constraints(browser_agent, tmp_path):
    agent = browser_agent
    store = agent.checkpoint_store = Store(tmp_path / 'checkpoints.db')
    job, cv, config = store.add_job(opportunity()), resume_file(tmp_path), Config()
    await route_html(agent, '<form><label>Preferred shift<select required><option value="">Choose</option><option>Day</option><option>Night</option></select></label><label>Resume<input type=file required></label><button>Submit application</button></form>')
    result = await agent.apply(job, Profile(), cv, config, lambda: False)
    assert result.state == 'needs_input'
    details = result.field_details[0]
    assert details['label'] == 'Preferred shift'
    assert details['options'] == ['Day', 'Night']
    assert details['required'] is True
    saved = Store(store.path).checkpoint(job.id)
    assert saved['field_details'][0]['key'] == details['key']


@pytest.mark.asyncio
async def test_checkpoint_approval_is_invalid_when_step_options_change(browser_agent, tmp_path):
    agent = browser_agent
    store = agent.checkpoint_store = Store(tmp_path / 'checkpoints.db')
    job, cv, config = store.add_job(opportunity()), resume_file(tmp_path), Config()
    options = '<option>Blue</option><option>Red</option>'
    async def route(r):
        await r.fulfill(content_type='text/html', body='<form><label>Favorite color<select required><option value=""></option>' + options + '</select></label><label>Resume<input type=file required></label><button>Submit application</button></form>')
    await agent.context.route('**/*', route)
    await agent.apply(job, Profile(), cv, config, lambda: False)
    await agent.page.locator('select').select_option(label='Blue')
    snapshot = await agent.get_continuation(job, config, cv)
    await agent.confirm_continuation(job, config, cv, snapshot['token'])
    options = '<option>Blue</option><option>Green</option>'
    agent.pending_application = None
    await agent.page.goto('about:blank')
    result = await agent.apply(job, Profile(), cv, config, lambda: pytest.fail('stale step approval submitted'))
    assert result.state == 'needs_input'
