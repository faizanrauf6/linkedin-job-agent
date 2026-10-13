from types import SimpleNamespace
import pytest
from agent.forms import fill_form
from agent.models import Profile, Resume
from agent.profile_records import ApplicantRecords
from test_application_reliability import browser_agent
from test_application_reliability import opportunity,resume_file,route_html
from agent.document_library import DocumentLibrary
from agent.store import Store
from agent.models import Config


@pytest.mark.asyncio
async def test_repeating_employment_records_fill_each_distinct_role(browser_agent):
    page=browser_agent.page
    await page.set_content('<form><fieldset><legend>Employment 1</legend><label>Employer<input required></label><label>Job title<input required></label><label>Start date<input type=date required></label></fieldset><fieldset><legend>Employment 2</legend><label>Employer<input required></label><label>Job title<input required></label><label>Start date<input type=date required></label></fieldset></form>')
    profile=Profile(records=ApplicantRecords(employment=[{'organization':'Clinic A','title':'Nurse','start_date':'2020-01-01'}, {'organization':'Clinic B','title':'Senior nurse','start_date':'2023-02-01'}]))
    result=await fill_form(page.locator('form'),profile,Resume(label='CV'))
    assert not result.missing
    assert await page.locator('input').evaluate_all('els=>els.map(el=>el.value)')==['Clinic A','Nurse','2020-01-01','Clinic B','Senior nurse','2023-02-01']
    assert len(result.answers)==6


@pytest.mark.asyncio
async def test_native_multiselect_uses_all_confirmed_options(browser_agent):
    page=browser_agent.page
    await page.set_content('<form><label>Preferred departments<select required multiple><option>Emergency</option><option>Surgery</option><option>Outpatient</option></select></label></form>')
    result=await fill_form(page.locator('form'),Profile(saved_answers={'Preferred departments':'Emergency, Surgery'}),Resume(label='CV'))
    assert not result.missing
    assert await page.locator('select').evaluate('el=>[...el.selectedOptions].map(o=>o.textContent)')==['Emergency','Surgery']


@pytest.mark.asyncio
async def test_conditional_required_question_is_reported_before_submission(browser_agent):
    page=browser_agent.page
    await page.set_content('''<form><label>Have a license?<select required onchange="document.querySelector('#extra').innerHTML='<label>License jurisdiction<input required></label>'"><option value=""></option><option>Yes</option><option>No</option></select></label><div id=extra></div></form>''')
    result=await fill_form(page.locator('form'),Profile(saved_answers={'Have a license?':'Yes'}),Resume(label='CV'))
    assert 'License jurisdiction' in result.missing
    assert result.field_details[0]['label']=='License jurisdiction'


@pytest.mark.asyncio
async def test_missing_repeating_record_group_is_added_before_filling(browser_agent):
    page=browser_agent.page
    await page.set_content('''<form><fieldset><legend>Employment 1</legend><label>Employer<input required></label></fieldset><button type=button onclick="const f=document.querySelector('fieldset').cloneNode(true);f.querySelector('legend').textContent='Employment 2';this.before(f)">Add employment</button></form>''')
    profile=Profile(records=ApplicantRecords(employment=[{'organization':'Clinic A'},{'organization':'Clinic B'}]))
    result=await fill_form(page.locator('form'),profile,Resume(label='CV'))
    assert not result.missing
    assert await page.locator('input').evaluate_all('els=>els.map(el=>el.value)')==['Clinic A','Clinic B']


@pytest.mark.asyncio
async def test_accessible_multiselect_verifies_all_selected_options(browser_agent):
    page=browser_agent.page
    await page.set_content('''<form><div role=listbox aria-label=Departments aria-multiselectable=true aria-required=true><div role=option aria-selected=false onclick="this.setAttribute('aria-selected',this.getAttribute('aria-selected')!=='true')">Emergency</div><div role=option aria-selected=false onclick="this.setAttribute('aria-selected',this.getAttribute('aria-selected')!=='true')">Surgery</div></div></form>''')
    result=await fill_form(page.locator('form'),Profile(saved_answers={'Departments':'Emergency, Surgery'}),Resume(label='CV'))
    assert not result.missing
    assert await page.get_by_role('option',selected=True).count()==2


@pytest.mark.asyncio
async def test_confirmed_additional_document_is_uploaded_separately_from_cv(browser_agent,tmp_path):
    agent=browser_agent
    store=agent.checkpoint_store=Store(tmp_path/'agent.db')
    library=DocumentLibrary(store,tmp_path)
    document=library.import_document(b'Confirmed nursing license original','license.txt','License','license')
    document.confirmed=True;library.save(document)
    job=store.add_job(opportunity())
    await route_html(agent,'''<form onsubmit="event.preventDefault();window.docs=[...this.querySelectorAll('[type=file]')].map(el=>el.files[0].name);document.body.innerHTML='Application submitted successfully!'"><label>Resume<input type=file required></label><label>License<input type=file required></label><button>Submit application</button></form>''')
    result=await agent.apply(job,Profile(),resume_file(tmp_path),Config(),lambda:True)
    assert result.state=='submitted'
    assert await agent.page.evaluate('window.docs')==['selected.pdf','license.txt']
    assert job.document_ids==[document.id]
