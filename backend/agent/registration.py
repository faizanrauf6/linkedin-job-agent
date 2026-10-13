"""Explicit exact-host registration; never signs attestations or bypasses challenges."""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import string
from urllib.parse import urljoin, urlsplit

import keyring

from .forms import FIELD_INFO, act
from .models import ApplyResult
from .security import hostname, public_url

REGISTER = re.compile(r'^(?:Create (?:an )?account|Sign up|Register)$', re.I)
ATTESTATION = re.compile(r'\bsignature\b|\bsign here\b|\bi (?:certify|attest|declare|agree|accept)\b', re.I)


async def registration_form(frame):
    forms = frame.locator('form:visible')
    for index in range(await forms.count()):
        form = forms.nth(index)
        button = form.get_by_role('button', name=REGISTER).first
        if await form.locator('input[type=password]:visible').count() and await button.count() and await button.is_visible():
            return form
    return None


def strong_password() -> str:
    alphabet = string.ascii_letters + string.digits + '!@#$%^&*_-+'
    while True:
        password = ''.join(secrets.choice(alphabet) for _ in range(32))
        if all(any(c in group for c in password) for group in [string.ascii_lowercase, string.ascii_uppercase, string.digits, '!@#$%^&*_-+']):
            return password


async def register_account(frame, form, profile, settings, can_act, dry_run=False):
    host = hostname(frame.url)
    if not settings.allow_registration or host not in settings.registration_hosts:
        return ApplyResult(state='needs_input', reason='Registration requires opt-in for this exact verified portal in Settings')
    if dry_run:
        return ApplyResult(state='needs_input', reason='Registration requires a separate account creation step; dry run creates no accounts')
    if not profile.email:
        return ApplyResult(state='needs_input', reason='Registration requires your confirmed email address')
    action = urljoin(frame.url, await form.get_attribute('action') or frame.url)
    if urlsplit(frame.url).scheme != 'https' or urlsplit(action).scheme != 'https':
        return ApplyResult(state='needs_input', reason='Registration requires HTTPS for both the page and credential destination')
    if not public_url(action, resolve=False) or hostname(action) != host:
        return ApplyResult(state='needs_input', reason='Registration credentials cannot be sent to a different host')
    fields = form.locator('input:not([type=hidden]):not([type=submit]):not([type=button]),select,textarea,[role=checkbox]')
    planned = []
    passwords = 0
    for index in range(await fields.count()):
        field = fields.nth(index)
        info = await field.evaluate(FIELD_INFO)
        if info['disabled'] or not await field.is_visible():
            continue
        label, kind = info['label'], info['type']
        if ATTESTATION.search(label) or kind in {'checkbox', 'radio'}:
            if info['required'] or ATTESTATION.search(label):
                return ApplyResult(state='needs_input', reason='Registration requires your personal consent or attestation', questions=[label])
            continue
        if kind == 'password':
            passwords += 1
            planned.append((field, None))
        elif kind == 'email' or re.search(r'\be.?mail\b|^username$', label, re.I):
            planned.append((field, profile.email))
        elif re.search(r'^first name$', label, re.I) and profile.first_name:
            planned.append((field, profile.first_name))
        elif re.search(r'^last name$', label, re.I) and profile.last_name:
            planned.append((field, profile.last_name))
        elif re.search(r'^(?:full )?name$', label, re.I) and profile.first_name and profile.last_name:
            planned.append((field, profile.first_name + ' ' + profile.last_name))
        elif info['required']:
            return ApplyResult(state='needs_input', reason='Registration has an unsupported required field', questions=[label])
    if not passwords:
        return ApplyResult(state='needs_input', reason='Registration password form is unsupported')
    if not can_act():
        return ApplyResult(state='needs_input', reason='Agent paused or configuration changed')
    # Reuse an earlier generated credential after an interrupted signup. Never
    # overwrite a password that may already belong to an external account.
    try:
        raw = await asyncio.to_thread(keyring.get_password, 'linkedin-local-agent:portal', host)
        creds = json.loads(raw) if raw else {'username': profile.email, 'password': strong_password(), 'registration_generated': True, 'registration_pending': False}
        if raw and (not creds.get('registration_generated') or creds.get('registration_pending') or creds.get('registration_complete')):
            return ApplyResult(state='needs_input', reason='An existing account or prior registration attempt requires review. Sign in or verify the account; account creation will not be repeated')
        if creds.get('username') != profile.email or not creds.get('password'):
            return ApplyResult(state='needs_input', reason='Saved exact-host credentials need review before registration')
        if not raw:
            await asyncio.to_thread(keyring.set_password, 'linkedin-local-agent:portal', host, json.dumps(creds))
    except Exception:
        return ApplyResult(state='needs_input', reason='Credentials could not be saved securely; registration was not started')
    for field, value in planned:
        await act(can_act, lambda: field.fill(creds['password'] if value is None else value))
        if not await field.evaluate('el => el.checkValidity ? el.checkValidity() : true'):
            return ApplyResult(state='needs_input', reason='Registration values require review; account was not submitted')
    # Persist uncertainty before the external click. A timeout, crash, or later
    # run must never create another account automatically.
    creds['registration_pending'] = True
    try:
        await asyncio.to_thread(keyring.set_password, 'linkedin-local-agent:portal', host, json.dumps(creds))
    except Exception:
        return ApplyResult(state='needs_input', reason='Registration state could not be saved securely; account creation was not submitted')
    button = form.get_by_role('button', name=REGISTER).first
    await act(can_act, lambda: button.click())
    return ApplyResult(state='needs_input', reason='Account creation attempted. Complete email verification, MFA, or CAPTCHA in the browser, then retry the application')
