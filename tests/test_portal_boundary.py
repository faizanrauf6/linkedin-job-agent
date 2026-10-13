from types import SimpleNamespace
import pytest
from agent.browser import BrowserAgent
from agent.verification import ats_tenant
from agent.verification import verify_destination
from agent.models import Settings
from test_verification_reliability import vacancy,site,OFFICIAL


@pytest.mark.asyncio
@pytest.mark.parametrize('url',['https://jobs.ashbyhq.com/other/role','https://jobs.ashbyhq.com/other%2Frole'])
async def test_verified_shared_ats_host_does_not_allow_a_different_employer_tenant(tmp_path,monkeypatch,url):
    agent=BrowserAgent(tmp_path)
    agent.sensitive_hosts={'jobs.ashbyhq.com'}
    agent.application_tenants={'jobs.ashbyhq.com':ats_tenant('https://jobs.ashbyhq.com/acme/role')}
    monkeypatch.setattr('agent.browser.public_url',lambda *_args,**_kwargs:True)
    aborted=[]
    async def abort():aborted.append(True)
    async def fetch(**_):pytest.fail('Cross-tenant request reached the network')
    request=SimpleNamespace(url=url,is_navigation_request=lambda:True)
    route=SimpleNamespace(request=request,abort=abort,fetch=fetch)
    await agent._route(route)
    assert aborted==[True]


@pytest.mark.asyncio
async def test_embedded_ats_tenant_evidence_survives_verification():
    target=OFFICIAL+'/careers/apply'
    pages={OFFICIAL:f'<a href="{target}">Engineer</a>',target:'<iframe src="https://jobs.ashbyhq.com/acme/role"></iframe>'}
    result=await verify_destination(vacancy(target),Settings(),fetch=site(pages))
    assert result.state=='eligible'
    assert result.allowed_tenants['jobs.ashbyhq.com']==[('ashby','jobs.ashbyhq.com','acme')]
