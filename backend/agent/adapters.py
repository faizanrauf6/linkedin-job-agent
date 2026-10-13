"""Small portal adapters. Every frame and entry URL stays inside verified hosts."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from urllib.parse import urljoin

from .forms import act
from .attachments import SAVED_RESUME_CARDS
from .security import hostname, public_url


@dataclass(frozen=True)
class PortalAdapter:
    name: str
    selector: str
    version: str = "1"

    async def scope(self, frame):
        candidates = frame.locator(self.selector)
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            if await candidate.is_visible():
                if await candidate.locator('input,textarea,select,[role="combobox"],[role="radio"]').count():
                    return candidate
                actions = candidate.get_by_role('button', name=re.compile(r'^(?:Next|Continue|Review(?: your application)?|Submit(?: (?:your )?application)?|Send application)$', re.I))
                if await actions.count() or await candidate.get_by_text(CONFIRMATION).count():
                    return candidate
        return None

    async def classify(self, scope):
        confirmations = scope.get_by_text(CONFIRMATION)
        if any([await confirmations.nth(i).is_visible() for i in range(await confirmations.count())]):
            return 'confirmation'
        fields = scope.locator('input:not([type=hidden]),textarea,select,[role=combobox],[role=radio]')
        if any([await fields.nth(i).is_visible() for i in range(await fields.count())]):
            return 'editable'
        return 'review'

    async def reuse_resume(self, scope, resume, download, can_act, proofs=None):
        """A saved document is usable only after its bytes match the original."""
        if self.name not in SAVED_RESUME_CARDS or not resume.sha256:
            return False
        cards = scope.locator(SAVED_RESUME_CARDS[self.name])
        for index in range(await cards.count()):
            card = cards.nth(index)
            if not await card.is_visible() or resume.filename not in await card.inner_text():
                continue
            links = card.locator('a[href]')
            for n in range(await links.count()):
                link = links.nth(n)
                href = await link.get_attribute('href')
                base = await link.evaluate('el => el.ownerDocument.URL')
                data = await download(urljoin(base, href or ''))
                if data is None or hashlib.sha256(data).hexdigest() != resume.sha256:
                    continue
                radio = card.locator('input[type=radio],[role=radio]').first
                selected = False
                if await radio.count():
                    if await radio.get_attribute('role') == 'radio':
                        if await radio.get_attribute('aria-checked') != 'true':
                            await act(can_act, radio.click)
                        selected = await radio.get_attribute('aria-checked') == 'true'
                    else:
                        await act(can_act, radio.check)
                        selected = await radio.is_checked()
                # Greenhouse/Lever single-attachment controls need no selection.
                elif self.name != 'linkedin':
                    selected = True
                if selected:
                    if proofs is not None:
                        proofs.append({'kind': 'saved', 'link': await link.element_handle(),
                            'radio': await radio.element_handle() if await radio.count() else None,
                            'url': urljoin(base, href or '')})
                    return True
        return False

    async def enter(self, frame, allowed_hosts: set[str], can_act):
        # Do not click a submit-like control merely because it says Apply.
        if await self.scope(frame) is not None:
            return
        entries = frame.get_by_role('link', name=re.compile(r'^Apply(?: for this job| now)?$', re.I))
        for index in range(await entries.count()):
            entry = entries.nth(index)
            target = urljoin(frame.url, await entry.get_attribute('href') or '')
            if await entry.is_visible() and hostname(target) in allowed_hosts and public_url(target, resolve=False):
                await act(can_act, lambda: frame.goto(target, wait_until='domcontentloaded', timeout=30000))
                return
        buttons = frame.get_by_role('button', name=re.compile(r'^Apply (?:for this job|now)$', re.I))
        for index in range(await buttons.count()):
            button = buttons.nth(index)
            if await button.is_visible():
                await act(can_act, lambda: button.click())
                return


CONFIRMATION = re.compile(r'^(?:Your application was sent(?: to .+)?|Application submitted(?: successfully)?[.!]?|Thank you for applying(?:[.!]| to .+)?|Your application has been (?:received|submitted)[.!]?|You have successfully applied[.!]?)$', re.I)

GENERIC = PortalAdapter('generic', 'form:visible')
GREENHOUSE = PortalAdapter('greenhouse', '#application_form:visible,#application form:visible,form:visible')
LEVER = PortalAdapter('lever', 'form.application-form:visible,form:visible')
LINKEDIN = PortalAdapter('linkedin', '[role="dialog"]:visible,.jobs-easy-apply-modal:visible')


def adapter_for(url: str, easy_apply: bool = False) -> PortalAdapter:
    if easy_apply:
        return LINKEDIN
    host = hostname(url)
    if host in {'boards.greenhouse.io', 'job-boards.greenhouse.io', 'boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io'}:
        return GREENHOUSE
    if host in {'jobs.lever.co', 'jobs.eu.lever.co'}:
        return LEVER
    return GENERIC


async def verified_scopes(page, allowed_hosts: set[str], easy_apply: bool = False):
    """Ignore even visible frames unless employer evidence explicitly allowed them."""
    result = []
    for frame in list(page.frames):
        if hostname(frame.url) not in allowed_hosts or not public_url(frame.url, resolve=False):
            continue
        adapter = adapter_for(frame.url, easy_apply and frame == page.main_frame)
        scope = await adapter.scope(frame)
        if scope is not None:
            result.append((adapter, scope, frame))
    # Prefer a dedicated portal form over an unrelated employer newsletter form.
    return sorted(result, key=lambda item: item[0].name == 'generic')
