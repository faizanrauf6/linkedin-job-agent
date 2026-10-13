import pytest
from agent.models import Answer,Profile,Resume
from agent.forms import fill_form
from test_application_reliability import browser_agent


@pytest.mark.asyncio
async def test_narrative_fits_whole_confirmed_sentences_without_truncating_facts(browser_agent):
    page=browser_agent.page
    await page.set_content('<form><label>Tell us about your experience<textarea required maxlength=48></textarea></label></form>')
    async def narrator(_):return Answer(value='I managed patient intake safely. I supervised the emergency nursing team for five years.',source='confirmed_profile_facts:summary')
    result=await fill_form(page.locator('form'),Profile(),Resume(label='CV'),narrator=narrator)
    assert not result.missing
    assert await page.locator('textarea').input_value()=='I managed patient intake safely.'
    assert result.answers['Tell us about your experience']=='I managed patient intake safely.'


@pytest.mark.asyncio
async def test_factual_values_are_never_silently_truncated_by_the_portal(browser_agent):
    page=browser_agent.page
    await page.set_content('<form><label>Email<input required maxlength=5></label></form>')
    result=await fill_form(page.locator('form'),Profile(email='ada@example.com'),Resume(label='CV'))
    assert result.missing
    assert not result.answers
