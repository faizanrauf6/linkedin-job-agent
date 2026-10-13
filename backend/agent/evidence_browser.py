"""Render public careers evidence in a separate, disposable browser."""
import asyncio
import html
from urllib.parse import urljoin

from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

from .security import host_matches, hostname, public_url
from .verification import MAX_HTML_BYTES, ats_tenant, bounded_html


async def render_evidence(pw, url: str) -> str:
    """Return rendered HTML without loading any retained applicant context.

    All requests are public GET/HEAD requests. Documents keep the initial
    employer domain or exact supported ATS tenant; public resource origins may
    differ. Every redirect is separately validated before browser release.
    """
    owner = hostname(url)
    tenant = ats_tenant(url)

    def owned(destination):
        if tenant is not None:
            return ats_tenant(destination) == tenant
        return host_matches(hostname(destination), owner)

    browser = None
    blocked_document = []
    document_redirects = 0
    document_generation = 0
    try:
        async with asyncio.timeout(18):
            if not await asyncio.to_thread(public_url, url):
                raise ValueError("Evidence destination is not public")
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(service_workers="block", accept_downloads=False)
            page = await context.new_page()
            pending_documents = set()
            documents_idle = asyncio.Event()
            documents_idle.set()

            def document_finished(request):
                pending_documents.discard(request)
                if not pending_documents:
                    documents_idle.set()

            page.on("requestfinished", document_finished)
            page.on("requestfailed", document_finished)

            async def close_popup(popup):
                await popup.close()

            context.on("page", close_popup)

            async def close_socket(socket):
                await socket.close()

            await context.route_web_socket("**/*", close_socket)

            async def route_evidence(route):
                nonlocal document_redirects, document_generation
                request = route.request
                response = None
                navigation = request.is_navigation_request()
                try:
                    if navigation and request.frame == page.main_frame:
                        document_generation += 1
                        pending_documents.add(request)
                        documents_idle.clear()
                    public = await asyncio.to_thread(public_url, request.url)
                    if request.method not in {"GET", "HEAD"} or not public:
                        if navigation:
                            blocked_document.append("Evidence navigation is not a public GET")
                        await route.abort()
                        return
                    if navigation and (request.frame.page != page or not owned(request.url)):
                        blocked_document.append("Evidence navigation changed document ownership or ATS tenant")
                        await route.abort()
                        return
                    resource_url = request.url
                    for hop in range(5):
                        response = await route.fetch(url=resource_url, max_redirects=0, max_retries=0, timeout=10000)
                        if response.status not in {301, 302, 303, 307, 308}:
                            break
                        location = response.headers.get("location", "")
                        destination = urljoin(resource_url, location)
                        if not location or not await asyncio.to_thread(public_url, destination) or navigation and not owned(destination):
                            if navigation:
                                blocked_document.append("Evidence redirect changed ownership, ATS tenant or public-address scope")
                            await route.abort()
                            return
                        if navigation:
                            document_redirects += 1
                            if document_redirects > 5:
                                raise ValueError("Evidence document redirect limit exceeded")
                            # Chromium may bypass routing after an HTTP redirect.
                            # An inert document issues a fresh checked navigation.
                            await route.fulfill(status=200, content_type="text/html", body='<meta http-equiv="refresh" content="0;url=' + html.escape(destination, quote=True) + '">')
                            return
                        if hop == 4:
                            raise ValueError("Evidence resource redirect limit exceeded")
                        # Subresources cannot use a meta refresh; fetch every hop
                        # explicitly after checking its public address.
                        await response.dispose()
                        response = None
                        resource_url = destination
                    is_html = "html" in response.headers.get("content-type", "").casefold()
                    if is_html:
                        declared = response.headers.get("content-length", "")
                        if declared.isdigit() and int(declared) > MAX_HTML_BYTES:
                            raise ValueError("Evidence HTML exceeds inspection limit")
                        body = await response.body()
                        if len(body) > MAX_HTML_BYTES:
                            raise ValueError("Evidence HTML exceeds inspection limit")
                        await route.fulfill(response=response, body=body)
                    else:
                        await route.fulfill(response=response)
                except Exception as exc:
                    if navigation:
                        blocked_document.append(str(exc))
                    await route.abort()
                finally:
                    if response is not None:
                        await response.dispose()

            await context.route("**/*", route_evidence)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=12000)
                idle_document = page.url, document_generation
                try:
                    await page.wait_for_load_state("networkidle", timeout=2500)
                    idle_document = page.url, document_generation
                except PlaywrightTimeout:
                    pass
            except Exception:
                if blocked_document:
                    raise ValueError(blocked_document[-1]) from None
                raise
            while True:
                # Network-idle can time out while a meta refresh is still
                # awaiting its next document. Complete that document and its
                # DOM before taking evidence, within the existing outer deadline.
                await documents_idle.wait()
                await page.wait_for_load_state("domcontentloaded", timeout=12000)
                if pending_documents:
                    continue
                capture_url, capture_generation = page.url, document_generation
                if not owned(capture_url):
                    raise ValueError(blocked_document[-1] if blocked_document else "Rendered evidence changed document ownership or ATS tenant")
                if (capture_url, capture_generation) != idle_document:
                    # A final document may arrive after the first idle wait
                    # expired. Give its scripts/resources their own bounded
                    # settling wait before capturing the resulting role links.
                    idle_document = capture_url, capture_generation
                    try:
                        await page.wait_for_load_state("networkidle", timeout=2500)
                    except PlaywrightTimeout:
                        pass
                    continue
                # Resolve links in an inert snapshot against the final document
                # base; the verifier's HTML-only callback retains the entry URL.
                try:
                    snapshot = await page.evaluate("""() => {
                        const copy = document.documentElement.cloneNode(true);
                        for (const node of copy.querySelectorAll('a[href],iframe[src]')) {
                            const attribute = node.tagName === 'A' ? 'href' : 'src';
                            try { node.setAttribute(attribute, new URL(node.getAttribute(attribute), document.baseURI).href); }
                            catch (_) {}
                        }
                        return '<!doctype html>' + copy.outerHTML;
                    }""")
                except PlaywrightError as exc:
                    if "execution context was destroyed" not in str(exc).casefold():
                        raise
                    await asyncio.sleep(0)
                    continue
                # Navigation or history changes during capture invalidate the
                # snapshot; every retry rechecks document ownership and tenant.
                if pending_documents or document_generation != capture_generation or page.url != capture_url:
                    continue
                return bounded_html(snapshot)
    finally:
        if browser is not None:
            await asyncio.wait_for(browser.close(), timeout=2)
