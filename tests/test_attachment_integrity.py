import pytest
from agent.forms import fill_form
from agent.models import Profile,Config
from agent.store import Store
from agent.document_library import DocumentLibrary
from test_application_reliability import browser_agent,resume_file,opportunity,route_html


@pytest.mark.asyncio
async def test_license_explicitly_excluding_cv_never_receives_the_resume(browser_agent,tmp_path):
    page=browser_agent.page
    await page.set_content('<form><label>Professional license (not CV)<input type=file required></label></form>')
    result=await fill_form(page.locator('form'),Profile(),resume_file(tmp_path))
    assert result.missing
    assert await page.locator('input').input_value()==''


@pytest.mark.asyncio
async def test_each_current_cv_widget_is_verified_independently(browser_agent,tmp_path):
    page=browser_agent.page;cv=resume_file(tmp_path)
    await page.set_content('<form><label>Primary Resume<input type=file required></label><label>Secondary Resume<input type=file required></label></form>')
    await page.locator('input').nth(0).set_input_files(cv.path)
    wrong=tmp_path/'wrong.pdf';wrong.write_bytes(b'wrong')
    await page.locator('input').nth(1).set_input_files(str(wrong))
    result=await fill_form(page.locator('form'),Profile(),cv,attachment_verified=True)
    assert not result.missing
    assert await page.locator('input').nth(1).evaluate('el=>el.files[0].name')=='selected.pdf'


@pytest.mark.asyncio
async def test_duplicate_document_labels_keep_both_upload_proofs(browser_agent,tmp_path):
    agent=browser_agent;store=agent.checkpoint_store=Store(tmp_path/'agent.db')
    library=DocumentLibrary(store,tmp_path)
    document=library.import_document(b'Confirmed nursing license original','license.txt','License','license')
    document.confirmed=True;library.save(document)
    job=store.add_job(opportunity())
    await route_html(agent,'''<form><label>Resume<input type=file required></label><label>License<input id=first type=file required></label><label>License<input type=file required onchange="const d=new DataTransfer();d.items.add(new File(['wrong'],'wrong.txt'));document.querySelector('#first').files=d.files"></label><button>Submit application</button></form>''')
    result=await agent.apply(job,Profile(),resume_file(tmp_path),Config(),lambda:pytest.fail('Unverified earlier duplicate upload reserved'))
    assert result.state=='needs_input'
