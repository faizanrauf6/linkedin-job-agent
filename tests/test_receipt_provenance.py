"""Receipt locations must identify the document where confirmation was observed."""
import pytest

from agent.models import Config, Profile
from test_application_reliability import browser_agent, opportunity, resume_file


@pytest.mark.asyncio
@pytest.mark.parametrize('embedded',[False,True])
async def test_receipt_uses_confirmation_document_after_parent_navigation(browser_agent,tmp_path,embedded):
    agent=browser_agent
    application='https://careers.example/apply'
    confirmation='https://careers.example/confirmation'
    iframe='https://jobs.lever.co/acme/123'
    form='<form onsubmit="event.preventDefault();window.top.location.href=\''+confirmation+'\'"><label>Email<input required type=email></label><label>Resume<input required type=file></label><button>Submit application</button></form>'
    async def route(request):
        url=request.request.url
        if url==confirmation:
            html='<p>Application submitted successfully!</p>'
        elif url==application:
            html='<iframe src="'+iframe+'"></iframe>' if embedded else form
        elif url==iframe:
            html=form
        else:
            pytest.fail('Unexpected fixture request: '+url)
        await request.fulfill(content_type='text/html',body=html)
    await agent.context.route('**/*',route)
    reservations=[]
    result=await agent.apply(opportunity(),Profile(email='ada@example.com'),resume_file(tmp_path),Config(),lambda:reservations.append(True) or True)
    assert result.state=='submitted'
    assert reservations==[True]
    assert result.receipt['text']=='Application submitted successfully!'
    assert result.receipt['url']==confirmation
