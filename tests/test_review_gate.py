import pytest
from agent.approvals import ApprovalStore
from agent.models import Config,Profile
from agent.store import Store
from test_application_reliability import browser_agent,opportunity,resume_file,route_html


@pytest.mark.asyncio
async def test_review_mode_cannot_send_an_unreviewed_actual_payload(browser_agent,tmp_path):
    agent=browser_agent
    store=agent.checkpoint_store=Store(tmp_path/'agent.db')
    cv=resume_file(tmp_path);store.save_resume(cv)
    config=store.config();config.settings.paused=False;config.profile.email='ada@example.com';config=store.save_config(config)
    job=opportunity();job.status='needs_input';job.resume_id=cv.id;job.resume_hash=cv.sha256
    job=store.add_job(job)
    approvals=ApprovalStore(store,tmp_path)
    agent.review_payload=approvals.payload_matches
    await route_html(agent,'<form onsubmit="event.preventDefault();document.body.innerHTML=\'Application submitted successfully!\'"><label>Email<input required></label><label>Resume<input type=file required></label><button>Submit application</button></form>')
    result=await agent.apply(job,config.profile,cv,config,lambda:pytest.fail('Unreviewed payload reserved'))
    assert result.state=='needs_input'
    job.answers,job.answer_sources=result.answers,result.sources
    store.save_job(job)
    snapshot=approvals.create(job.id)
    approvals.approve(job.id,snapshot['token'],True)
    sent=[]
    result=await agent.apply(store.job(job.id),config.profile,cv,config,lambda:sent.append(True) or True)
    assert result.state=='submitted'
    assert sent==[True]


@pytest.mark.asyncio
async def test_changed_form_payload_requires_a_new_approval(browser_agent,tmp_path):
    agent=browser_agent
    store=agent.checkpoint_store=Store(tmp_path/'agent.db')
    cv=resume_file(tmp_path);store.save_resume(cv)
    config=store.config();config.settings.paused=False;config.profile.email='ada@example.com';config.profile.phone='+15551234567';config=store.save_config(config)
    job=opportunity();job.status='needs_input';job.resume_id=cv.id;job.resume_hash=cv.sha256;job.answers={'Email':'ada@example.com'};job.answer_sources={'Email':'profile:email'}
    job=store.add_job(job)
    approvals=ApprovalStore(store,tmp_path);snapshot=approvals.create(job.id);approvals.approve(job.id,snapshot['token'],True)
    agent.review_payload=approvals.payload_matches
    await route_html(agent,'<form><label>Email<input required></label><label>Phone<input required></label><label>Resume<input type=file required></label><button>Submit application</button></form>')
    result=await agent.apply(store.job(job.id),config.profile,cv,config,lambda:pytest.fail('Changed payload reserved'))
    assert result.state=='needs_input'
    assert result.answers['Phone']=='+15551234567'
