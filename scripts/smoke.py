"""Start an isolated temporary test instance. Requires port 8765 to be free."""
import asyncio
import socket
import tempfile
from pathlib import Path

import uvicorn
from docx import Document
from playwright.async_api import async_playwright

from agent.api import create_app


async def browser_smoke(cv_path):
    output = Path(".runtime/smoke")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    document = Document(); document.add_paragraph("Alex Example. React TypeScript frontend engineer with four years of experience."); document.save(cv_path)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width":1440,"height":1100}, device_scale_factor=1)
        page.on("pageerror", lambda err: errors.append(str(err)))
        await page.goto("http://127.0.0.1:8765")
        await page.get_by_role("heading", name="A little less searching. A lot more possibility.").wait_for()
        await page.screenshot(path=str(output / "overview-desktop.png"), full_page=True)
        await page.get_by_role("button", name="My profile", exact=True).click()
        await page.get_by_label("First name", exact=True).fill("Alex")
        await page.get_by_label("Last name", exact=True).fill("Example")
        await page.get_by_label("Email address", exact=True).fill("alex@example.com")
        await page.get_by_label("Country", exact=True).fill("Canada")
        await page.get_by_label("Skills and technology stacks").fill("React, TypeScript")
        await page.get_by_role("button", name="Add employment record", exact=True).click()
        await page.get_by_label("Employer name", exact=True).fill("Example Employer")
        await page.get_by_label("Job title", exact=True).fill("Frontend engineer")
        await page.get_by_label("Start date", exact=True).fill("2022-01-01")
        await page.get_by_label("I currently work here").select_option("true")
        await page.get_by_role("button", name="Save changes", exact=True).click()
        await page.get_by_role("status").filter(has_text="Workspace updated").wait_for()
        await page.get_by_role("button", name="CV library", exact=True).click()
        await page.locator('input[type="file"]').first.set_input_files(cv_path)
        await page.get_by_text("Review needed", exact=True).wait_for()
        await page.get_by_text("I reviewed the text and confirm it is accurate.").click()
        await page.get_by_role("button", name="Save CV", exact=True).click()
        await page.get_by_text("Ready to use", exact=True).wait_for()
        supporting = cv_path.parent / "license.txt"
        supporting.write_text("Confirmed professional license fixture", encoding="utf-8")
        await page.get_by_label("Document label").fill("License")
        await page.get_by_label("Document type").select_option("license")
        await page.get_by_label("Supporting document file").set_input_files(supporting)
        await page.get_by_role("button", name="Upload supporting document", exact=True).click()
        await page.get_by_text("I reviewed License and approve using this original for applications.", exact=True).click()
        await page.get_by_text("Ready to use", exact=True).nth(1).wait_for()
        await page.get_by_role("button", name="Searches & filters", exact=True).click()
        await page.get_by_role("button", name="New search", exact=True).click()
        await page.get_by_label("Search name", exact=True).fill("Frontend in Canada")
        await page.get_by_label("Target job titles").fill("Frontend Engineer")
        await page.get_by_label("Countries", exact=False).fill("Canada")
        await page.get_by_role("button", name="Save changes", exact=True).click()
        await page.get_by_role("heading", name="Frontend in Canada", exact=True).wait_for()
        await page.get_by_role("button", name="Start agent", exact=True).click()
        await page.get_by_role("button", name="Pause agent", exact=True).wait_for()
        await page.get_by_role("button", name="Pause agent", exact=True).click()
        await page.get_by_role("button", name="Overview", exact=True).click()
        await page.get_by_role("heading", name="Good to see you, Alex.").wait_for()
        await page.screenshot(path=str(output / "overview-configured.png"), full_page=True)
        await page.set_viewport_size({"width":390,"height":844})
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(output / "overview-mobile.png"), full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile horizontal overflow"
        await page.get_by_role("button", name="Open navigation").click()
        await page.get_by_role("button", name="Settings", exact=True).click()
        await page.get_by_role("heading", name="Settings", exact=True).wait_for()
        await page.get_by_role("heading", name="Live connection checks", exact=True).wait_for()
        await page.get_by_text("Not checked yet", exact=True).first.wait_for()
        advanced = page.locator('form').filter(has=page.get_by_label('Maximum search pages', exact=True))
        await advanced.get_by_label('Maximum search pages', exact=True).fill('4')
        await advanced.get_by_label('Technology aliases').fill('ReactJS = React')
        await advanced.get_by_label('Approved account creation portals').fill('careers.example.com')
        await advanced.get_by_role('button', name='Save changes', exact=True).click()
        await page.get_by_role('status').filter(has_text='Workspace updated').wait_for()
        stored = await (await page.request.get('http://127.0.0.1:8765/api/config')).json()
        assert stored['settings']['max_search_pages'] == 4
        assert stored['settings']['technology_aliases'] == {'reactjs': 'react'}
        assert stored['settings']['registration_hosts'] == ['careers.example.com']
        assert not stored['settings']['allow_registration'], 'Portal listing must not enable account creation'
        assert not stored['settings']['prevent_sleep'], 'Smoke must not activate sleep prevention'
        assert stored['profile']['records']['employment'][0]['organization'] == 'Example Employer'
        await page.get_by_role("heading", name="Backup and restore", exact=True).wait_for()
        backup = await page.request.get('http://127.0.0.1:8765/api/maintenance/backup')
        assert backup.ok and (await backup.body()).startswith(b'PK')
        assert (await page.request.get('http://127.0.0.1:8765/api/email/connection')).ok
        assert not errors, errors
        await browser.close()
    print("Dashboard smoke passed: structured profile, CV and supporting-document confirmation, searches, start/pause, mobile navigation, recovery/alias/portal settings, live-check empty states, private backup and mailbox default; no browser errors.")


async def main():
    with socket.socket() as listener, tempfile.TemporaryDirectory(prefix="orbit-smoke-") as directory:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", 8765))
        except OSError as exc:
            raise SystemExit("Stop the running agent before the isolated smoke test; port 8765 is in use.") from exc
        listener.listen(128)
        runtime = Path(directory)
        server = uvicorn.Server(uvicorn.Config(create_app(runtime, run_worker=False), log_level="error", access_log=False))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            for _ in range(100):
                if server.started:
                    break
                if task.done():
                    await task
                    raise RuntimeError("Isolated smoke server did not start")
                await asyncio.sleep(0.05)
            if not server.started:
                raise RuntimeError("Isolated smoke server timed out")
            await browser_smoke(runtime / "frontend.docx")
        finally:
            server.should_exit = True
            await task


if __name__ == "__main__":
    asyncio.run(main())
