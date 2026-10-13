import asyncio
import re
from collections import deque
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx

from .matching import contains
from .models import Job, Settings, Verification
from .security import host_matches, hostname, public_url
from .store import canonical_url
from .adapters import adapter_for


MAX_EVIDENCE_PAGES = 12
MAX_EVIDENCE_DEPTH = 4
MAX_RENDERED_PAGES = 4
MAX_HTML_BYTES = 1_500_000
CAREERS_LINK = re.compile(r"career|vacanc|jobs|join[ -]?(?:us|our)|open (?:roles|positions)", re.I)


def ats_tenant(url: str) -> tuple[str, str, str] | None:
    """Supported shared hosts establish no ownership without a company tenant."""
    adapter = adapter_for(url).name
    if adapter not in {"greenhouse", "lever"}:
        return None
    parsed = urlsplit(url)
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    if not segments or any(part in {".", ".."} or "/" in part or "\\" in part for part in segments):
        return None
    if adapter == "greenhouse" and segments[0] == "embed":
        tenants = parse_qs(parsed.query).get("for", [])
        tenant = tenants[0] if len(tenants) == 1 else ""
    else:
        tenant = segments[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", tenant) or tenant.casefold() in {"embed", "jobs", "api"}:
        return None
    # Keep region/hostname exact as well as the tenant; a shared host is never
    # sufficient to approve another company's listing.
    return adapter, hostname(url), tenant.casefold()


def bounded_html(html: str) -> str:
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("Careers page exceeds inspection limit")
    return html


def public_links(base: str, links: list[str]) -> list[str]:
    result = []
    for link in links:
        try:
            destination = urljoin(base, link)
        except ValueError:
            continue
        if public_url(destination, resolve=False):
            result.append(destination)
    return list(dict.fromkeys(result))


class Links(HTMLParser):
    def __init__(self, text: str):
        super().__init__()
        self.links: list[str] = []
        self.text: list[str] = []
        self.embeds: list[str] = []
        self.career_links: list[str] = []
        self.has_scripts = False
        self.ignored_tag = ""
        self.anchor = ""
        self.anchor_text: list[str] = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.has_scripts |= tag == "script"
            self.ignored_tag = tag
        if self.ignored_tag:
            return
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
                self.anchor, self.anchor_text = href, []

        if tag == "iframe":
            src = dict(attrs).get("src")
            if src:
                self.embeds.append(src)

    def handle_endtag(self, tag):
        if tag == self.ignored_tag:
            self.ignored_tag = ""
        if tag == "a" and self.anchor:
            if CAREERS_LINK.search(self.anchor + " " + " ".join(self.anchor_text)):
                self.career_links.append(self.anchor)
            self.anchor = ""

    def handle_data(self, data):
        if not self.ignored_tag:
            self.text.append(data)
            if self.anchor:
                self.anchor_text.append(data)


async def fetch_html(url: str) -> str:
    original_host = hostname(url)
    original_tenant = ats_tenant(url)
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        for _ in range(5):
            if not await asyncio.to_thread(public_url, url):
                raise ValueError("Destination is not a public website")
            async with client.stream("GET", url, headers={"User-Agent": "LocalJobAgent/0.1"}) as response:
                if response.is_redirect:
                    target = urljoin(url, response.headers.get("location", ""))
                    # Careers evidence cannot silently change ownership.
                    if not host_matches(hostname(target), original_host) and not host_matches(original_host, hostname(target)):
                        raise ValueError("Careers page redirected outside the employer domain")
                    if original_tenant and ats_tenant(target) != original_tenant:
                        raise ValueError("Careers page redirected outside the linked ATS tenant")
                    url = target
                    continue
                response.raise_for_status()
                if "html" not in response.headers.get("content-type", "text/html"):
                    raise ValueError("Expected an HTML careers page")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_HTML_BYTES:
                        raise ValueError("Careers page exceeds inspection limit")
                return content.decode("utf-8", errors="replace")
    raise ValueError("Too many redirects")


RISK_PATTERNS = {
    "Payment solicitation": r"\bapplication fees?\b|\bpay to apply\b|\bsend (?:us )?cryptocurrency\b|\bpay[^.!?\n]{0,90}to (?:get|secure) (?:the |a )?job\b|\b(?:wire|pay|send|transfer)\b[^.!?\n]{0,50}\b(?:deposit|upfront payment|processing fee)\b",
    "Sensitive financial information": r"\bbank (?:login|password)\b|\b(?:send|provide|share|upload)\b[^.!?\n]{0,60}\b(?:bank account details|credit card|bank statement)\b",
    "Sensitive identity document request": r"\b(?:upload|send|share|provide)\b[^.!?\n]{0,60}\b(?:passport|national id|social security (?:number|card)|identity document|driver.?s licen[cs]e)\b",
    "Unrealistic employment promise": r"\bguaranteed (?:employment|job|hiring|income)\b|\b(?:guarantee|promise)\b[^.!?\n]{0,35}\b(?:employment|job|hiring)\b|\bno interview required\b[^.!?\n]{0,60}\b(?:high salary|high income|earn)\b",
}


def employer_risk_flags(text: str) -> list[str]:
    flags = []
    for label, pattern in RISK_PATTERNS.items():
        for match in re.finditer(pattern, text, re.I):
            # Negation belongs to its sentence/clause; unrelated earlier safety
            # language must not hide a subsequent request for money or documents.
            preceding = re.split(r'[.!?;,\n]|\bbut\b|\bhowever\b', text[max(0, match.start() - 120):match.start()], flags=re.I)[-1].casefold()
            if re.search(r"\b(?:never|will not|do not|don't|does not|doesn't|cannot)\b", preceding) or re.search(r'\bno\s*$', preceding):
                continue
            flags.append(label)
            break
    return flags


def suspicious_text(text: str) -> bool:
    return bool(employer_risk_flags(text))


def scoped_hosts(settings: Settings, portal: str, allowed: list[str]) -> list[str]:
    approved = settings.sso_hosts.get(portal, [])
    return list(dict.fromkeys(allowed + [host for host in approved if public_url('https://' + host, resolve=False) and hostname('https://' + host) == host]))


async def verified_application_hosts(target, company_host, settings, fetch, pages, evidence):
    allowed = [company_host, hostname(target)]
    # An iframe receives applicant data only when its exact URL is embedded by
    # the employer's already-corroborated application page, and is a supported ATS.
    if host_matches(hostname(target), company_host):
        document = next((html for base, html in pages if canonical_url(base) == canonical_url(target)), None)
        if document is None:
            try:
                document = await fetch(target)
            except (ValueError, httpx.HTTPError, OSError):
                document = ''
        for url in public_links(target, Links(document).embeds):
            if ats_tenant(url) is not None:
                allowed.append(hostname(url))
                evidence.append('Employer application page embeds supported portal: ' + target + ' -> ' + url)
                allowed = scoped_hosts(settings, hostname(url), allowed)
    return scoped_hosts(settings, hostname(target), allowed)


async def verify_destination(
    job: Job, settings: Settings, fetch=fetch_html, *,
    render: Callable[[str], Awaitable[str]] | None = None,
) -> Verification:
    """Corroborate destinations through bounded, employer-rooted public evidence.

    ``render(url) -> HTML`` is an optional asynchronous fallback for script or
    empty pages. Its caller must use an isolated context without applicant data
    or cookies and restrict every request/redirect to public addresses, keeping
    document ownership/ATS tenant intact. Rendering grants no additional trust.
    """
    risk_flags = employer_risk_flags(job.description)
    job.risk_flags = list(dict.fromkeys(job.risk_flags + risk_flags))
    if risk_flags:
        return Verification(state="blocked", evidence=["Employer warning: " + flag for flag in risk_flags])
    target = job.application_url or job.url
    if target and not public_url(target, resolve=False):
        return Verification(state="blocked", evidence=["Unsafe application URL"])
    trusted = next((domain for company, domain in settings.trusted_employers.items() if company.strip().casefold() == job.company.strip().casefold()), "")
    official = ("https://" + trusted.strip().rstrip("/")) if trusted else job.company_website
    if not official or not public_url(official, resolve=False):
        return Verification(state="unresolved", evidence=["Employer website is unknown. Add the exact company and official domain in Settings."])
    company_host = hostname(official)
    evidence = ["User-specified employer domain: " + company_host] if trusted else ["Company website reported by LinkedIn: " + company_host]
    pages: list[tuple[str, str]] = []
    fetched: set[str] = set()
    queued = {canonical_url(official)}
    # Entries retain their ownership chain, so unrelated crawled pages never
    # contribute authority to a different ATS tenant.
    pending = deque([(official, 0, None, [])])
    render_pending = deque()
    rendered = 0
    company_identity = linkedin_company_key(job.company_linkedin_url)
    identity_source = ""
    role_evidence = None
    inspected_provenance = []

    async def bounded_fetch(url):
        key = canonical_url(url)
        if key in fetched or len(fetched) >= MAX_EVIDENCE_PAGES:
            raise ValueError("Careers evidence page budget exhausted")
        fetched.add(key)
        return bounded_html(await fetch(url))

    while pending or render_pending:
        if pending:
            base, depth, tenant, chain = pending.popleft()
            try:
                html = await bounded_fetch(base)
            except (httpx.HTTPError, ValueError, OSError):
                continue
            parsed = Links(html)
            if render is not None and (parsed.has_scripts or not parsed.links):
                render_pending.append((base, depth, tenant, chain))
        else:
            if render is None or rendered >= MAX_RENDERED_PAGES:
                break
            base, depth, tenant, chain = render_pending.popleft()
            rendered += 1
            try:
                html = bounded_html(await asyncio.wait_for(render(base), timeout=20))
            except Exception:
                # A missing browser or failed render leaves public evidence
                # unresolved rather than granting applicant-data access.
                continue
            parsed = Links(html)
            chain = chain + ["Rendered public careers evidence: " + base]
        for item in chain:
            if item not in inspected_provenance:
                inspected_provenance.append(item)
        # Prefer the rendered document when the target itself embeds a portal.
        pages = [(url, doc) for url, doc in pages if canonical_url(url) != canonical_url(base)]
        pages.append((base, html))
        destinations = public_links(base, parsed.links + parsed.embeds)
        if tenant is None and company_identity and any(linkedin_company_key(link) == company_identity for link in destinations):
            identity_source = base
        if job.easy_apply:
            text = " ".join(parsed.text)
            if contains(text, job.title) or job.requisition and contains(text, job.requisition):
                role_evidence = chain + ["Matching role appears on employer-linked careers page: " + base]
            if identity_source and role_evidence is not None:
                return Verification(state="eligible", evidence=evidence + ["Exact LinkedIn company profile is linked from employer website: " + identity_source] + role_evidence,
                                    allowed_hosts=scoped_hosts(settings, "www.linkedin.com", ["www.linkedin.com", company_host]))
        elif target:
            # An ATS board may substantiate only destinations within its own
            # explicitly linked tenant, even on a shared hostname.
            owned = [link for link in destinations if public_url(link, resolve=False) and (tenant is None or ats_tenant(link) == tenant)]
            exact = any(canonical_url(link) == canonical_url(target) for link in owned)
            official_path = tenant is None and host_matches(hostname(target), company_host) and any(
                urlsplit(link).path.rstrip("/") == urlsplit(target).path.rstrip("/") and host_matches(hostname(link), company_host)
                for link in owned)
            if exact or official_path:
                proof = evidence + chain + ["Exact application destination linked from employer website: " + base + " -> " + target]
                allowed = await verified_application_hosts(target, company_host, settings, bounded_fetch, pages, proof)
                return Verification(state="eligible", evidence=proof, allowed_hosts=allowed)
        if depth >= MAX_EVIDENCE_DEPTH:
            continue
        careers = set(public_links(base, parsed.career_links))
        # Prioritize supported boards and careers links ahead of incidental
        # same-domain links without exceeding the overall document budget.
        destinations.sort(key=lambda link: (ats_tenant(link) is None, link not in careers and not CAREERS_LINK.search(link)))
        for destination in destinations:
            key = canonical_url(destination)
            if key in queued or not public_url(destination, resolve=False):
                continue
            next_tenant = ats_tenant(destination)
            if tenant is not None:
                if next_tenant != tenant:
                    continue
            elif host_matches(hostname(destination), company_host):
                # Start with careers-oriented root links; below those, follow
                # bounded same-domain navigation to nested role/team pages.
                if depth == 0 and destination not in careers and not CAREERS_LINK.search(destination):
                    continue
                next_tenant = None
            elif next_tenant is None:
                continue
            if len(queued) >= MAX_EVIDENCE_PAGES * 4:
                break
            queued.add(key)
            transition = "Employer-linked careers evidence: " + base + " -> " + destination
            if tenant is None and next_tenant is not None:
                transition = "Employer links supported portal tenant " + next_tenant[2] + ": " + base + " -> " + destination
            pending.append((destination, depth + 1, next_tenant, chain + [transition]))
    return Verification(state="unresolved", evidence=evidence + inspected_provenance + ["No matching employer careers listing or exact application link was found within inspection limits"])


def linkedin_company_key(url: str) -> str:
    if hostname(url) not in {"www.linkedin.com", "linkedin.com"}:
        return ""
    match = re.match(r"/company/([^/]+)", urlsplit(url).path, re.I)
    return match[1].casefold() if match else ""
