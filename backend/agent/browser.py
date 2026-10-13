from __future__ import annotations

import asyncio
import base64
import json
import re
import html
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import keyring
from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout, Error as BrowserError

from .forms import fill_form, act, OperationPaused, form_signature
from .model_gateway import narrative_answer
from .models import ApplyResult, Job
from .security import hostname, public_url
from .verification import verify_destination, employer_risk_flags
from .adapters import adapter_for, verified_scopes, CONFIRMATION
from .discovery import discover_candidates, acknowledge_candidates
from .continuation import Continuation
from .attachments import attachments_valid, capture_uploads
from .evidence_browser import render_evidence
from .registration import registration_form, register_account

COUNTRIES = ["United States", "United Kingdom", "Canada", "Germany", "Pakistan", "India", "Australia", "Netherlands", "France", "Ireland", "Spain", "United Arab Emirates", "Saudi Arabia", "Singapore", "Sweden", "Norway", "Denmark", "Finland", "Poland", "Portugal", "Switzerland", "Austria", "Belgium", "Italy", "New Zealand", "South Africa", "Brazil", "Mexico", "Japan", "China", "Bangladesh", "Philippines", "Indonesia", "Malaysia", "Turkey", "Türkiye", "Romania", "Czechia"]
CHALLENGE = re.compile(r"verify (?:you are|you're) human|security verification|enter (?:the |your )?verification code|two.factor authentication|check your (?:email|inbox)|verify your email|confirm your email|email verification|captcha", re.I)


def compensation_metadata(text: str) -> dict:
    values = {}
    currency = r"USD|EUR|GBP|CAD|AUD|NZD|SGD|PKR|INR|AED|SAR|CHF|£|€"
    amount = r"\d[\d,]*(?:\.\d+)?[kK]?"
    salaries = list(re.finditer(r"(?<!\w)(" + currency + r")\s*[$£€]?\s*(" + amount + r")\s*(?:[-–—]\s*(?:(?:" + currency + r")\s*)?[$£€]?\s*(" + amount + r"))?\s*(?:/|per\s+)(year|annum|yr|month|mo|hour|hr)\b", text, re.I))
    salary = salaries[0] if len(salaries) == 1 else None
    declared_currencies = { {"£": "GBP", "€": "EUR"}.get(v, v.upper()) for v in re.findall(r"\b(?:USD|EUR|GBP|CAD|AUD|NZD|SGD|PKR|INR|AED|SAR|CHF)\b|[£€]", text, re.I) }
    if salary and len(declared_currencies) == 1:
        def number(value):
            return float(value.rstrip("kK").replace(",", "")) * (1000 if value[-1:].lower() == "k" else 1)
        low, high = number(salary[2]), number(salary[3] or salary[2])
        if low <= high:
            values.update(salary_min=low, salary_max=high, salary_currency={"£": "GBP", "€": "EUR"}.get(salary[1], salary[1].upper()), salary_period={"annum": "year", "yr": "year", "mo": "month", "hr": "hour"}.get(salary[4].lower(), salary[4].lower()))
    if re.search(r"\bno (?:visa )?sponsorship\b|\b(?:do not|cannot|can not|don't) (?:offer|provide|support) (?:visa )?sponsorship\b|\b(?:visa )?sponsorship (?:is )?not (?:available|provided|offered)\b|\bvisa sponsorship available\s*:\s*(?:no|false)\b", text, re.I):
        values["sponsorship_available"] = False
    elif re.search(r"\b(?:visa sponsorship (?:is )?(?:available|provided|offered)|we (?:offer|provide) visa sponsorship)(?:\s*:\s*(?:yes|true))?\s*(?=[.;!\n]|$)", text, re.I):
        values["sponsorship_available"] = True
    return values


def metadata(text: str, search) -> dict:
    values = compensation_metadata(text)
    for country in list(dict.fromkeys(search.countries + COUNTRIES)):
        if re.search(r"(?<!\w)" + re.escape(country) + r"(?!\w)", text, re.I):
            values["country"] = country
            break
    for city in search.cities:
        if re.search(r"(?<!\w)" + re.escape(city) + r"(?!\w)", text, re.I):
            values["city"] = city
            break
    for place in ("remote", "hybrid", "on-site", "onsite"):
        if re.search(r"\b" + place + r"\b", text, re.I):
            values["workplace"] = "onsite" if place == "on-site" else place
            break
    for level in ["internship", "entry level", "associate", "mid-senior level", "director", "executive"]:
        if level in text.casefold():
            values["seniority"] = level
            break
    for kind in ["full-time", "part-time", "contract", "temporary", "internship", "volunteer"]:
        if kind in text.casefold():
            values["employment_type"] = kind
            break
    age = re.search(r"(\d+)\s*(minute|hour|day|week|month)s?\s+ago", text, re.I)
    if age:
        values["posted_days"] = int(age[1]) * {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30}[age[2].lower()]
    values["location"] = text[:350]
    return values


class BrowserAgent:
    def __init__(self, runtime: Path, headless: bool = False, store=None):
        self.runtime = runtime
        self.headless = headless
        self.pw = self.context = self.page = None
        self.lock = asyncio.Lock()
        self.sensitive_hosts: set[str] | None = None
        self.credential_host: str | None = None
        self.credential_allowed_hosts: set[str] = set()
        self.credential_redirect = None
        self.credential_request_failed = False
        self.pending_application = None
        self.checkpoint_store = store
        self.state = "not_open"

    async def ensure(self):
        if self.context:
            return
        self.pw = await async_playwright().start()
        directory = self.runtime / "browser"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.context = await self.pw.chromium.launch_persistent_context(
                str(directory), headless=self.headless, accept_downloads=False,
                service_workers="block", viewport={"width": 1360, "height": 950},
                args=["--disable-features=AutofillServerCommunication,PasswordManagerOnboarding"],
            )
            self.context.set_default_timeout(8000)
            await self.context.route("**/*", self._route)
            await self.context.route_web_socket("**/*", lambda ws: ws.close())
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.state = "open"
        except BaseException:
            await self.pw.stop()
            self.pw = self.context = self.page = None
            raise

    async def _route(self, route):
        url = route.request.url
        if not await asyncio.to_thread(public_url, url):
            await route.abort()
            return
        if self.sensitive_hosts is not None and hostname(url) not in self.sensitive_hosts:
            await route.abort()
            return
        if self.credential_host is not None and (hostname(url) != self.credential_host or urlsplit(url).scheme != 'https'):
            await route.abort()
            return
        # route.continue_ does not re-run interception for HTTP redirect chains.
        # Fetch one hop and handle redirects without forwarding sensitive bodies.
        response = None
        try:
            response = await route.fetch(max_redirects=0, timeout=20000)
            if response.status in {301, 302, 303, 307, 308}:
                target = urljoin(url, response.headers.get("location", ""))
                if not await asyncio.to_thread(public_url, target) or self.sensitive_hosts is not None and hostname(target) not in self.sensitive_hosts and not (self.credential_host and hostname(target) in self.credential_allowed_hosts):
                    await route.abort()
                    return
                if self.credential_host is not None and (hostname(target) != self.credential_host or urlsplit(target).scheme != 'https'):
                    safe_get = response.status in {301, 302, 303} or route.request.method in {'GET', 'HEAD'}
                    if route.request.is_navigation_request() and safe_get and urlsplit(target).scheme == 'https' and hostname(target) in self.credential_allowed_hosts:
                        # Commit an inert response on the credential host. _login
                        # destroys this document before reopening approved hosts
                        # and issuing a fresh callback GET without the POST body.
                        self.credential_redirect = (route.request.frame, target)
                        await route.fulfill(status=200, content_type='text/html', body='<!doctype html><title>Completing sign-in</title>')
                    else:
                        if route.request.is_navigation_request():
                            self.credential_request_failed = True
                        await route.abort()
                    return
                method = route.request.method
                if method not in {"GET", "HEAD"} and response.status in {307, 308}:
                    await route.abort()
                    return
                if route.request.is_navigation_request():
                    # A fresh navigation makes the next hop a separately checked
                    # request and preserves URL/base-path semantics in the browser.
                    await route.fulfill(status=200, content_type="text/html", body='<meta http-equiv="refresh" content="0;url=' + html.escape(target, quote=True) + '">')
                else:
                    await route.abort()
                return
            await route.fulfill(response=response)
        except Exception:
            if self.credential_host is not None and route.request.is_navigation_request():
                self.credential_request_failed = True
            await route.abort()
        finally:
            if response is not None:
                await response.dispose()

    async def _reset_sensitive(self):
        self.pending_application = None
        if self.sensitive_hosts is not None:
            # Destroy every retained sensitive document before opening the network
            # boundary for discovery or another portal.
            for page in list(self.context.pages):
                if page != self.page:
                    await page.close()
            await self.page.goto("about:blank")
            self.sensitive_hosts = None
            self.credential_host = None
            self.credential_allowed_hosts = set()
            self.credential_redirect = None

    async def close(self):
        async with self.lock:
            if self.context:
                await self.context.close()
            if self.pw:
                await self.pw.stop()
            self.pw = self.context = self.page = None
            self.sensitive_hosts = None
            self.credential_host = None
            self.credential_allowed_hosts = set()
            self.credential_redirect = None
            self.state = "not_open"
            self.pending_application = None

    async def open_login(self):
        async with self.lock:
            await self.ensure()
            await self._reset_sensitive()
            await self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            await self.page.bring_to_front()
            self.state = "login_open"

    async def inspect(self):
        async with self.lock:
            await self.ensure()
            await self.page.bring_to_front()
            self.state = "open"

    async def _text(self, selector: str) -> str:
        locator = self.page.locator(selector).first
        return (await locator.inner_text()).strip() if await locator.count() else ""

    def _inspection_frames(self):
        if not self.page:
            return []
        return [frame for frame in self.page.frames if frame == self.page.main_frame or self.sensitive_hosts is not None and hostname(frame.url) in self.sensitive_hosts]

    def _confirmation_frames(self, frame=None):
        if frame is None:
            return self._inspection_frames()
        if not frame.is_detached():
            return [frame]
        # Some portals replace the top-level document after an embedded submit.
        main = self.page.main_frame
        return [main] if self.sensitive_hosts is not None and hostname(main.url) in self.sensitive_hosts else []

    async def confirmations(self, frame=None) -> set[str]:
        texts = set()
        for frame in self._confirmation_frames(frame):
            matches = frame.get_by_text(CONFIRMATION)
            for i in range(await matches.count()):
                element = matches.nth(i)
                if await element.is_visible():
                    texts.add((await element.text_content() or "").strip())
        return texts

    async def has_new_confirmation(self, previous: set[str], frame=None) -> bool:
        frames = self._confirmation_frames(frame)
        for candidate in frames:
            if await candidate.locator('form:visible input:invalid,form:visible select:invalid,form:visible textarea:invalid').count():
                return False
        return bool(await self.confirmations(frame) - previous)

    async def _challenge(self) -> bool:
        for frame in self._inspection_frames():
            if any(part in frame.url for part in ["/checkpoint/", "/challenge/", "/authwall"]):
                return True
            body = frame.locator('body')
            if await body.count() and CHALLENGE.search((await body.inner_text())[:12000]):
                return True
            if await frame.locator('iframe[src*="captcha"]').count():
                return True
        return False

    async def _login(self, can_act=lambda: True, frame=None) -> bool:
        frame = frame or self.page.main_frame
        password = frame.locator('input[type="password"]:visible').first
        if not await password.count():
            return True
        host = hostname(frame.url)
        if urlsplit(frame.url).scheme != 'https' or not public_url(frame.url, resolve=False) or self.sensitive_hosts is not None and host not in self.sensitive_hosts:
            return False
        # Validate the form destination before releasing any credentials, even
        # when a second SSO host was explicitly approved for application traffic.
        form = password.locator('xpath=ancestor::form[1]')
        if not await form.count():
            return False
        target = urljoin(frame.url, await form.get_attribute('action') or frame.url)
        if urlsplit(target).scheme != 'https' or not public_url(target, resolve=False) or hostname(target) != host:
            return False
        username = form.locator('input[type="email"]:visible,input[name="username"]:visible,input[autocomplete="username"]:visible,input#username:visible').first
        button = form.get_by_role("button", name=re.compile(r"^(?:Sign in|Log in|Login)$", re.I)).first
        if not await username.count() or not await button.count():
            return False
        try:
            raw = await asyncio.to_thread(keyring.get_password, "linkedin-local-agent:portal", host)
            creds = json.loads(raw) if raw else {}
            if not isinstance(creds.get('username'), str) or not isinstance(creds.get('password'), str) or not creds['username'] or not creds['password']:
                return False
        except Exception:
            return False
        # Keep the exact-host boundary until this sensitive document is reset.
        # Cross-host SSO requiring credential forwarding remains a manual step.
        self.credential_allowed_hosts = set(self.sensitive_hosts or {host})
        self.credential_redirect = None
        self.credential_request_failed = False
        self.sensitive_hosts = {host}
        self.credential_host = host
        await act(can_act, lambda: username.fill(creds["username"]))
        await act(can_act, lambda: password.fill(creds["password"]))
        await act(can_act, lambda: button.click())
        await self.page.wait_for_timeout(1500)
        await frame.wait_for_load_state('domcontentloaded', timeout=10000)
        has_password = await frame.locator('input[type="password"]:visible').count()
        challenged = await self._challenge()
        # Read redirect state after DOM inspection: route interception may have
        # completed while those awaits resolved the post-login document.
        redirected = self.credential_redirect is not None and self.credential_redirect[0] == frame
        ordinary_target = frame.url
        ordinary_success = not self.credential_request_failed and not frame.is_detached() and urlsplit(ordinary_target).scheme == 'https' and hostname(ordinary_target) == host and not has_password and not challenged
        if redirected or ordinary_success:
            target = self.credential_redirect[1] if redirected else ordinary_target
            approved = set(self.credential_allowed_hosts)
            # No broadening while a credential document or its scripts survives.
            # Same-origin parents, siblings, and popups may retain form values.
            # Destroy the whole page and close other pages before reopening SSO.
            for other_page in list(self.context.pages):
                if other_page != self.page:
                    await act(can_act, other_page.close)
            await act(can_act, lambda: self.page.goto('about:blank', wait_until='load'))
            frame = self.page.main_frame
            self.sensitive_hosts = approved
            self.credential_host = None
            self.credential_allowed_hosts = set()
            self.credential_redirect = None
            await act(can_act, lambda: frame.goto(target, wait_until='domcontentloaded', timeout=30000))
        logged_in = urlsplit(frame.url).scheme == 'https' and hostname(frame.url) in (self.sensitive_hosts or {host}) and not await frame.locator('input[type="password"]:visible').count() and not await self._challenge()
        if logged_in and creds.get('registration_generated') and creds.get('registration_pending'):
            creds.update(registration_pending=False, registration_complete=True)
            try:
                await asyncio.to_thread(keyring.set_password, "linkedin-local-agent:portal", host, json.dumps(creds))
            except Exception:
                # A pending flag left in Keychain remains safe: signup stays
                # blocked until a later supported login can reconcile it.
                pass
        return logged_in

    async def check_session(self) -> dict:
        """Inspect the existing page and session without logging in or submitting."""
        async with self.lock:
            if self.page is None or self.page.is_closed():
                return {"state": "not_open", "details": "Open the agent browser and sign in to check this session"}
            if await self._challenge():
                self.state = "needs_input"
                return {"state": self.state, "details": "Complete email verification, MFA, or CAPTCHA in the agent browser"}
            if "/login" in self.page.url or await self.page.locator('input[type="password"]:visible').count():
                self.state = "needs_login"
                return {"state": self.state, "details": "The current portal requires sign-in"}
            if hostname(self.page.url) in {"www.linkedin.com", "linkedin.com"}:
                signed_in = self.page.locator('.global-nav__me:visible,.global-nav a[href*="/mynetwork/"]:visible,nav[aria-label="Primary Navigation"] a[href*="/mynetwork/"]:visible')
                if await signed_in.count():
                    self.state = "connected"
                    return {"state": self.state, "details": "A signed-in LinkedIn page is visible; application access still depends on the selected portal"}
            self.state = "open"
            return {"state": self.state, "details": "Browser is open; a signed-in session has not been established by this page"}

    async def _search_cards(self, can_act):
        """Bound lazy loading before reading detail pages changes the main document."""
        previous, stable = None, 0
        cards = []
        for _ in range(10):
            cards = await self.page.locator('a[href*="/jobs/view/"]').evaluate_all("els => els.map(e=>({url:e.href,title:e.innerText}))")
            signature = tuple(sorted({card["url"] for card in cards}))
            stable = stable + 1 if signature == previous else 0
            if stable >= 2:
                break
            previous = signature
            await act(can_act, lambda: self.page.evaluate("""() => {
                const lists = document.querySelectorAll('.jobs-search-results-list,.scaffold-layout__list,[data-results-list]');
                for (const list of lists) list.scrollTop = list.scrollHeight;
                window.scrollTo(0,document.body.scrollHeight);
            }"""))
            await self.page.wait_for_timeout(350)
        return cards

    async def discover(self, search, limit: int, can_act=lambda: True, *, max_pages: int = 20, known_ids=None, progress=None) -> list[Job]:
        async with self.lock:
            await self.ensure()
            await self._reset_sensitive()
            if limit <= 0 or max_pages <= 0:
                return []
            async def load_page(query):
                await act(can_act, lambda: self.page.goto('https://www.linkedin.com/jobs/search/?' + urlencode(query.params), wait_until='domcontentloaded', timeout=30000))
                await self.page.wait_for_timeout(1200)
                if await self._challenge() or '/login' in self.page.url:
                    self.state = 'needs_login'
                    raise RuntimeError('LinkedIn needs sign-in or verification. Open the browser, finish verification, then search again.')
                cards = await self._search_cards(can_act)
                return [match[1] for card in cards if (match := re.search(r'/jobs/view/(?:[^/?]*-)?(\d+)', card['url']))]
            candidate_ids = await discover_candidates(search, limit, max_pages, known_ids or set(), progress if progress is not None else {}, load_page, can_act)
            jobs = []
            for id in candidate_ids:
                job = await self._read_job(id, search, can_act)
                if job:
                    jobs.append(job)
                elif progress is not None:
                    # A completed inspection without usable listing details is
                    # recorded separately from a transient navigation failure.
                    unavailable = progress.setdefault('unavailable_details', [])
                    unavailable.append({'linkedin_id': id, 'reason': 'No supported title/company details'})
                    progress['unavailable_details'] = unavailable[-100:]
                    acknowledge_candidates(progress, [id])
                if len(jobs) >= limit:
                    break
            self.state = "connected"
            return jobs

    async def _read_job(self, id: str, search, can_act=lambda: True) -> Job | None:
        url = "https://www.linkedin.com/jobs/view/" + id + "/"
        await act(can_act, lambda: self.page.goto(url, wait_until="domcontentloaded", timeout=30000))
        if await self._challenge():
            raise RuntimeError("LinkedIn verification required")
        await self.page.wait_for_timeout(600)
        title = await self._text("h1")
        company_locator = self.page.locator('.job-details-jobs-unified-top-card__company-name a,.jobs-unified-top-card__company-name a,a.topcard__org-name-link').first
        company = await company_locator.inner_text() if await company_locator.count() else ""
        company_url = await company_locator.get_attribute("href") if await company_locator.count() else ""
        if not title or not company:
            return None
        description = await self._text('#job-details,.jobs-description__content,.show-more-less-html__markup')
        primary = await self._text('.job-details-jobs-unified-top-card__primary-description-container,.jobs-unified-top-card__primary-description,.topcard__flavor-row')
        insights = " ".join(await self.page.locator('.job-details-jobs-unified-top-card__job-insight,.jobs-unified-top-card__job-insight').all_text_contents())
        easy = self.page.get_by_role("button", name=re.compile("Easy Apply", re.I)).first
        is_easy = await easy.count() > 0
        destination = ""
        if not is_easy:
            apply = self.page.locator('a[href]').filter(has_text=re.compile(r"^\s*Apply\s*$", re.I)).first
            if await apply.count():
                destination = await apply.get_attribute("href") or ""
            else:
                button = self.page.get_by_role("button", name=re.compile(r"^Apply(?: to .+)?$", re.I)).first
                if await button.count():
                    try:
                        async with self.page.expect_popup(timeout=5000) as popup_info:
                            await act(can_act, lambda: button.click())
                        popup = await popup_info.value
                        await popup.wait_for_load_state("domcontentloaded", timeout=15000)
                        destination = popup.url
                        await popup.close()
                    except BrowserTimeout:
                        if hostname(self.page.url) != "www.linkedin.com":
                            destination = self.page.url
        company_website = ""
        if company_url and "/company/" in company_url:
            about = await self.context.new_page()
            try:
                await act(can_act, lambda: about.goto(urljoin(url, company_url).split("?")[0].rstrip("/") + "/about/", wait_until="domcontentloaded", timeout=15000))
                links = await about.locator('a[href^="http"]').evaluate_all("els=>els.filter(e=>/website|visit website/i.test(e.innerText)||/company-website/i.test(e.getAttribute('data-control-name')||'')).map(e=>e.href)")
                company_website = next((link for link in links if hostname(link) not in {"www.linkedin.com", "linkedin.com"} and public_url(link, resolve=False)), "")
            except (BrowserTimeout, ValueError):
                pass
            finally:
                await about.close()
        poster = self.page.locator('.hirer-card__hirer-information a[href*="/in/"],.jobs-poster a[href*="/in/"],a[data-job-poster]').first
        poster_name = (await poster.inner_text()).strip() if await poster.count() and await poster.is_visible() else ""
        poster_url = urljoin(url, await poster.get_attribute('href') or '') if poster_name else ""
        poster_company = await self._text('.hirer-card__hirer-information .hirer-card__hirer-information-description,.jobs-poster__headline')
        badges = self.page.locator('[aria-label*="Verified"],[aria-label*="verified"],.hirer-card__verified-badge')
        verified_poster = any([await badges.nth(i).is_visible() for i in range(await badges.count())])
        requisition = re.search(r"(?:requisition|job (?:id|reference))\s*[:#]?\s*([A-Za-z0-9_-]{3,40})", description, re.I)
        details = metadata(primary + " " + insights, search)
        for key, value in compensation_metadata(description).items():
            details.setdefault(key, value)
        return Job(linkedin_id=id, title=title, company=company.strip(), description=description, url=url, application_url=destination, company_website=company_website, company_linkedin_url=urljoin(url, company_url or ""), requisition=requisition[1] if requisition else "", easy_apply=is_easy, poster_name=poster_name, poster_url=poster_url, poster_company=poster_company, verified_poster=verified_poster, risk_flags=employer_risk_flags(description), **details)

    async def verify(self, job, config):
        async def render(url):
            async with async_playwright() as pw:
                return await render_evidence(pw, url)
        return await verify_destination(job, config.settings, render=render)

    async def _download_resume(self, url, frame):
        if urlsplit(url).scheme != 'https' or hostname(url) not in (self.sensitive_hosts or set()) or not public_url(url, resolve=False):
            return None
        try:
            # Browser fetches pass through the same public-address, redirect and
            # applicant-host boundary as all other application traffic.
            encoded = await frame.evaluate('''async url => {
              const response = await fetch(url, {redirect:'error'});
              if (!response.ok || Number(response.headers.get('content-length')) > 10485760) return null;
              const reader = response.body.getReader(); const chunks = []; let size = 0;
              while (true) {
                const {value, done} = await reader.read(); if (done) break;
                size += value.length; if (size > 10485760) { await reader.cancel(); return null; }
                chunks.push(value);
              }
              let data = ''; for (const chunk of chunks) for (let i=0;i<chunk.length;i+=8192)
                data += String.fromCharCode(...chunk.subarray(i,i+8192));
              return btoa(data);
            }''', url)
            return base64.b64decode(encoded) if encoded is not None else None
        except Exception:
            return None

    async def get_continuation(self, job, config, resume):
        if self.pending_application is None:
            raise ValueError('No application is retained. Prepare this application first.')
        return await self.pending_application.snapshot(self.page, self.sensitive_hosts or set(), job, config, resume)

    async def confirm_continuation(self, job, config, resume, token):
        snapshot = await self.get_continuation(job, config, resume)
        if snapshot['token'] != token or not snapshot['fields']:
            raise ValueError('The browser answers changed or no completed answers can be confirmed. Review them again.')
        self.pending_application.approved = {item['key']: item['value'] for item in snapshot['fields']}
        self.pending_application.confirmed[snapshot['step_signature']] = {item['semantic_key']:item['value'] for item in snapshot['fields']}
        if self.checkpoint_store:
            self.checkpoint_store.save_checkpoint(job.id, self.pending_application.checkpoint(job, config, resume))
        return snapshot

    async def apply(self, job, profile, resume, config, before_submit, can_act=lambda: True, dry_run: bool = False) -> ApplyResult:
        async with self.lock:
            await self.ensure()
            retained = self.pending_application
            saved_checkpoint = self.checkpoint_store.checkpoint(job.id) if self.checkpoint_store else None
            continuing = retained is not None and await retained.valid(self.page, job, config, resume)
            if not continuing:
                await self._reset_sensitive()
            initial_adapter = adapter_for(job.url if job.easy_apply else job.application_url, job.easy_apply)
            job.adapter_name, job.adapter_version = initial_adapter.name, initial_adapter.version
            verification = await self.verify(job, config)
            if verification.state != "eligible":
                return ApplyResult(state="blocked", reason="Employer or destination evidence changed")
            self.sensitive_hosts = set(verification.allowed_hosts)
            answers = dict(retained.answers) if continuing else {}
            sources = dict(retained.sources) if continuing else {}
            uploaded = False
            submission_started = False
            try:
                target = job.url if job.easy_apply else job.application_url
                adapter = adapter_for(target, job.easy_apply)
                job.adapter_name, job.adapter_version = adapter.name, adapter.version
                if not target:
                    return ApplyResult(state="needs_input", reason="Application destination could not be discovered")
                if hostname(target) not in self.sensitive_hosts:
                    return ApplyResult(state="blocked", reason="Application host is outside verified employer evidence")
                if not continuing:
                    await act(can_act, lambda: self.page.goto(target, wait_until="domcontentloaded", timeout=30000))
                if await self._challenge():
                    return ApplyResult(state="needs_input", reason="Complete verification in the agent browser", portal_issue=True)
                # Registration is recognized before login, so stored passwords are
                # never silently used to create a second account.
                for frame in list(self.page.frames):
                    if hostname(frame.url) not in self.sensitive_hosts:
                        continue
                    signup = await registration_form(frame)
                    if signup is not None:
                        self.sensitive_hosts = {hostname(frame.url)}
                        self.credential_host = hostname(frame.url)
                        result = await register_account(frame, signup, profile, config.settings, can_act, dry_run)
                        result.portal_issue = True
                        return result
                for frame in list(self.page.frames):
                    if hostname(frame.url) in self.sensitive_hosts and not await self._login(can_act, frame):
                        return ApplyResult(state="needs_input", reason="Sign into this portal or configure exact-domain credentials", portal_issue=True)
                if job.easy_apply and not continuing:
                    button = self.page.get_by_role("button", name=re.compile("Easy Apply", re.I)).first
                    if not await button.count():
                        return ApplyResult(state="needs_input", reason="Easy Apply button is unavailable or application already exists")
                    await act(can_act, lambda: button.click())
                elif not continuing:
                    await adapter.enter(self.page.main_frame, self.sensitive_hosts, can_act)
                pending_transfer = None
                for step_index in range(20):
                    await self.page.wait_for_timeout(600)
                    if pending_transfer is not None:
                        for proof in pending_transfer:
                            element = proof['element'] if proof['kind'] == 'upload' else proof['link']
                            proof['transferred'] = not await element.evaluate('el => el.isConnected')
                        pending_transfer = None
                    if await self._challenge():
                        return ApplyResult(state="needs_input", reason="Verification required; finish it in the browser", answers=answers, sources=sources, portal_issue=True)
                    scopes = await verified_scopes(self.page, self.sensitive_hosts, job.easy_apply)
                    if not scopes:
                        return ApplyResult(state="needs_input", reason="No supported form found inside verified portal hosts", answers=answers, sources=sources)
                    adapter, scope, frame = scopes[0]
                    job.adapter_name, job.adapter_version = adapter.name, adapter.version
                    if await adapter.classify(scope) == 'confirmation':
                        return ApplyResult(state='needs_input', reason='An existing confirmation is visible. Check this application outcome before continuing.', answers=answers, sources=sources)
                    if self.pending_application is None:
                        self.pending_application = await Continuation.create(self.page, job, config, resume)
                        if self.pending_application.restore(saved_checkpoint, job, config, resume):
                            answers.update(self.pending_application.answers)
                            sources.update(self.pending_application.sources)
                    signature = await form_signature(scope)
                    self.pending_application.step = step_index
                    self.pending_application.step_signature = signature
                    self.pending_application.attachments = [proof for proof in self.pending_application.attachments
                        if await attachments_valid([proof], resume, scope, self._download_resume)]
                    uploaded = bool(self.pending_application.attachments)
                    if not uploaded:
                        uploaded = await adapter.reuse_resume(scope, resume, lambda url: self._download_resume(url, frame), can_act, self.pending_application.attachments)
                    outcome = await fill_form(scope, profile, resume, job.country, lambda question: narrative_answer(question, job, profile, resume, config.settings), can_act,
                        approved_prefilled=self.pending_application.approved, attachment_verified=uploaded,
                        confirmed_fields=self.pending_application.confirmed.get(signature, {}))
                    answers.update(outcome.answers)
                    sources.update(outcome.sources)
                    new_uploads = await capture_uploads(scope, resume)
                    if new_uploads:
                        self.pending_application.attachments = [proof for proof in self.pending_application.attachments if proof['kind'] != 'upload'] + new_uploads
                    uploaded = await attachments_valid(self.pending_application.attachments, resume, scope, self._download_resume)
                    self.pending_application.answers = dict(answers)
                    self.pending_application.sources = dict(sources)
                    self.pending_application.uploaded = uploaded
                    self.pending_application.field_details = outcome.field_details
                    if self.checkpoint_store:
                        self.checkpoint_store.save_checkpoint(job.id, self.pending_application.checkpoint(job, config, resume))
                    if outcome.missing:
                        return ApplyResult(state="needs_input", reason="Required answers or fields need attention", questions=outcome.missing, field_details=outcome.field_details, answers=answers, sources=sources)
                    submit = scope.get_by_role("button", name=getattr(adapter, 'submit_labels', re.compile(r"^(?:Submit(?: (?:your )?application)?|Send application)$", re.I))).first
                    if await submit.count() and await submit.is_visible():
                        if not uploaded:
                            return ApplyResult(state="needs_input", reason="The selected CV attachment has not been verified", answers=answers, sources=sources)
                        if dry_run:
                            return ApplyResult(state="needs_input", reason="Application prepared and ready for review; no submission was attempted", answers=answers, sources=sources)
                        prior_confirmations = await self.confirmations(frame)
                        if not await attachments_valid(self.pending_application.attachments, resume, scope, self._download_resume):
                            return ApplyResult(state='needs_input', reason='The selected CV attachment changed. Review the document before continuing.', answers=answers, sources=sources)
                        if not can_act() or not before_submit():
                            return ApplyResult(state="needs_input", reason="Submission cancelled: paused, changed settings, operating hours, or daily limit", answers=answers, sources=sources)
                        submission_started = True
                        await submit.click()
                        for _ in range(15):
                            if await self.has_new_confirmation(prior_confirmations, frame):
                                return ApplyResult(state="submitted", reason="Visible application confirmation received", answers=answers, sources=sources)
                            await self.page.wait_for_timeout(700)
                        return ApplyResult(state="submission_uncertain", reason="Submit was attempted but confirmation was not observed. Reconcile before retry.", answers=answers, sources=sources)
                    next_button = scope.get_by_role("button", name=getattr(adapter, 'next_labels', re.compile(r"^(?:Next|Continue|Review|Review your application)$", re.I))).first
                    if not await next_button.count():
                        return ApplyResult(state="needs_input", reason="Unsupported application step", answers=answers, sources=sources)
                    # A form without an explicit Next control cannot be advanced
                    # safely. Next is bounded, with final submission handled above.
                    transferred = [proof for proof in self.pending_application.attachments
                        if await attachments_valid([proof], resume, scope, self._download_resume)]
                    await act(can_act, lambda: next_button.click())
                    # Detached widgets may prove the next wizard step only when
                    # that observed agent transition actually removed them.
                    self.pending_application.attachments = transferred
                    pending_transfer = transferred
                return ApplyResult(state="needs_input", reason="Application exceeded the supported step limit", answers=answers, sources=sources)
            except OperationPaused:
                return ApplyResult(state="needs_input", reason="Agent paused or configuration changed", answers=answers, sources=sources)
            except BrowserError as exc:
                if submission_started:
                    return ApplyResult(state="submission_uncertain", reason="Submission started but the portal did not confirm the result. Reconcile before retry.", answers=answers, sources=sources)
                transient = isinstance(exc, BrowserTimeout) or bool(re.search(r'ERR_(?:CONNECTION|NETWORK|TIMED_OUT)|net::ERR_INTERNET_DISCONNECTED', str(exc)))
                return ApplyResult(state="failed", reason="Portal timed out before submission" if transient else "Portal browser interaction failed before submission", answers=answers, sources=sources, retryable=transient)
            finally:
                if submission_started:
                    self.pending_application = None
                    if self.checkpoint_store:
                        self.checkpoint_store.clear_checkpoint(job.id)
