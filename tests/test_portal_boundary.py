from types import SimpleNamespace
import pytest
from agent.browser import BrowserAgent
from agent.verification import ats_tenant


@pytest.mark.asyncio
async def test_verified_shared_ats_host_does_not_allow_a_different_employer_tenant(tmp_path,monkeypatch):
    agent=BrowserAgent(tmp_path)
    agent.sensitive_hosts={'jobs.ashbyhq.com'}
    agent.application_tenants={'jobs.ashbyhq.com':ats_tenant('https://jobs.ashbyhq.com/acme/role')}
    monkeypatch.setattr('agent.browser.public_url',lambda *_args,**_kwargs:True)
    aborted=[]
    async def abort():aborted.append(True)
    async def fetch(**_):pytest.fail('Cross-tenant request reached the network')
    request=SimpleNamespace(url='https://jobs.ashbyhq.com/other/role',is_navigation_request=lambda:True)
    route=SimpleNamespace(request=request,abort=abort,fetch=fetch)
    await agent._route(route)
    assert aborted==[True]
