import pytest
from agent.forms import fill_form,prefilled_candidates
from agent.models import Profile,Resume
from test_application_reliability import browser_agent


@pytest.mark.asyncio
async def test_distinct_confirmed_fields_with_same_label_are_all_recorded(browser_agent):
    page=browser_agent.page
    await page.set_content('<form><label>Preferred shift<input required value=Day></label><label>Preferred shift<input required value=Night></label></form>')
    scope=page.locator('form')
    fields=await prefilled_candidates(scope,Profile(),Resume(label='CV'),'')
    result=await fill_form(scope,Profile(),Resume(label='CV'),approved_prefilled={f['key']:f['value'] for f in fields})
    assert not result.missing
    assert len(result.answers)==2
    assert set(result.answers.values())=={'Day','Night'}
