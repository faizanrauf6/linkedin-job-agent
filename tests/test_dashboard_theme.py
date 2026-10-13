"""Exercise the built dashboard in Chromium without touching a real workspace."""
import mimetypes
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright, expect

from agent.api import create_app
from agent.models import Job

DIST = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
pytestmark = pytest.mark.skipif(not (DIST/'index.html').is_file(),reason='Build the dashboard first')


@pytest.fixture
async def dashboard(tmp_path):
    app = create_app(tmp_path,run_worker=False)
    app.state.store.add_job(Job(linkedin_id='theme-fixture',title='Clinic Nurse',company='Example Clinic',country='Canada',status='needs_input'))
    with TestClient(app) as client:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context(color_scheme='light')
            async def route(request):
                path = request.request.url.split('http://orbit.test',1)[-1].split('?',1)[0]
                if path.startswith('/api/'):
                    response = client.get(path)
                    await request.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','application/json'))
                else:
                    file = DIST/('index.html' if path=='/' else path.lstrip('/'))
                    assert file.resolve().is_relative_to(DIST.resolve())
                    await request.fulfill(body=file.read_bytes(),content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
            await context.route('http://orbit.test/**',route)
            page = await context.new_page()
            await page.goto('http://orbit.test/')
            yield page,context
            await browser.close()


def theme_button(page,theme):
    return page.get_by_role('button',name=theme.capitalize()+' theme',exact=True)


@pytest.mark.asyncio
async def test_theme_selection_persists_and_system_follows_device_changes(dashboard):
    page,context=dashboard
    await expect(theme_button(page,'system')).to_have_attribute('aria-pressed','true',timeout=3000)
    await expect(page.get_by_role('combobox',name='Color theme',exact=True)).to_have_count(0)
    await expect(page.locator('html')).to_have_attribute('data-theme','light')
    await theme_button(page,'dark').click()
    await expect(page.locator('html')).to_have_attribute('data-theme','dark')
    await page.reload()
    await expect(theme_button(page,'dark')).to_have_attribute('aria-pressed','true')
    await page.emulate_media(color_scheme='light')
    await expect(page.locator('html')).to_have_attribute('data-theme','dark')
    await theme_button(page,'light').click()
    await page.emulate_media(color_scheme='dark')
    await expect(page.locator('html')).to_have_attribute('data-theme','light')
    await theme_button(page,'system').click()
    await expect(page.locator('html')).to_have_attribute('data-theme','dark')
    await page.emulate_media(color_scheme='light')
    await expect(page.locator('html')).to_have_attribute('data-theme','light')
    other=await context.new_page()
    await other.goto('http://orbit.test/')
    await theme_button(other,'dark').click()
    await expect(theme_button(page,'dark')).to_have_attribute('aria-pressed','true')
    await expect(page.locator('html')).to_have_attribute('data-theme','dark')


@pytest.mark.asyncio
async def test_theme_survives_unavailable_storage_and_ignores_invalid_preferences(dashboard):
    page,context=dashboard
    await page.evaluate("localStorage.setItem('orbit.theme','invalid-theme')")
    await page.reload()
    await expect(theme_button(page,'system')).to_have_attribute('aria-pressed','true',timeout=3000)
    await expect(page.get_by_role('combobox',name='Color theme',exact=True)).to_have_count(0)
    await page.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Blocked','SecurityError')}})")
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    await page.reload()
    await theme_button(page,'dark').click()
    await expect(page.locator('html')).to_have_attribute('data-theme','dark')
    assert not errors


@pytest.mark.asyncio
async def test_dark_theme_covers_forms_tables_and_mobile_without_overflow(dashboard):
    page,context=dashboard
    await theme_button(page,'dark').click(timeout=3000)
    art_background=await page.locator('.floating-card').first.evaluate('el=>getComputedStyle(el).backgroundColor')
    assert max(map(int,art_background[4:-1].split(','))) < 80
    await page.get_by_role('button',name='My profile',exact=True).click()
    field=page.get_by_label('First name',exact=True)
    await expect(field).to_be_visible()
    colors=await field.evaluate("el=>({bg:getComputedStyle(el).backgroundColor,text:getComputedStyle(el).color})")
    assert colors['bg']!=colors['text']
    assert max(map(int,colors['bg'][4:-1].split(','))) < 80
    await page.get_by_role('button',name=re.compile(r'^Opportunities')).click()
    header=page.get_by_role('columnheader',name='ROLE & COMPANY')
    header_bg=await header.evaluate('el=>getComputedStyle(el).backgroundColor')
    assert max(map(int,header_bg[4:-1].split(','))) < 80
    await page.get_by_text('Clinic Nurse',exact=True).click()
    drawer=page.get_by_role('dialog',name='Application details')
    await expect(drawer).to_be_visible()
    drawer_bg=await drawer.evaluate('el=>getComputedStyle(el).backgroundColor')
    assert max(map(int,drawer_bg[4:-1].split(','))) < 80
    await page.get_by_role('button',name='Close details').click()
    await page.set_viewport_size({'width':390,'height':844})
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await expect(page.get_by_role('group',name='Color theme',exact=True)).to_be_visible()
    for theme in ['light','dark','system']:
        await expect(theme_button(page,theme)).to_be_visible()
    await page.set_viewport_size({'width':320,'height':700})
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
