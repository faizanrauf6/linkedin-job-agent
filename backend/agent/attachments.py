"""Document-bound evidence for the CV currently selected in an application."""
import hashlib
import re

from .forms import FIELD_INFO,is_resume_field

SAVED_RESUME_CARDS = {
    'linkedin': '.jobs-document-upload-redesign-card,.jobs-document-upload__container,[data-resume-id]',
    'greenhouse': '.uploaded-resume,#resume_attachment,.resume-upload',
    'lever': '.application-file,.uploaded-resume,.resume-upload',
}


async def upload_matches(handle, resume):
    try:
        return await handle.evaluate('''async (el, expected) => {
          if (el.ownerDocument.defaultView?.document !== el.ownerDocument) return false;
          const file = el.files?.[0];
          if (!file || el.files.length !== 1 || file.name !== expected.name || file.size > 10485760) return false;
          if (!expected.hash) return true;
          const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
          const hash = [...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('');
          return hash === expected.hash;
        }''', {'name': resume.filename, 'hash': resume.sha256})
    except Exception:
        return False


async def resume_inputs(scope):
    handles = []
    fields = scope.locator('input[type=file]')
    for index in range(await fields.count()):
        element = fields.nth(index)
        info = await element.evaluate(FIELD_INFO)
        if not is_resume_field(info['label']):
            continue
        handle = await element.element_handle()
        if handle is not None:
            handles.append(handle)
    return handles


async def capture_uploads(scope, resume):
    return [{'kind': 'upload', 'element': handle} for handle in await resume_inputs(scope)
            if await upload_matches(handle, resume)]


async def attachments_valid(proofs, resume, scope, download):
    # A retained FileList can survive a detached upload control. Current CV
    # widgets take precedence; removed controls only prove a later review step.
    inputs = await resume_inputs(scope)
    matches=[await upload_matches(handle,resume) for handle in inputs]
    matching_upload=bool(inputs) and all(matches)
    present=[await handle.evaluate('el=>!!el.files?.length') for handle in inputs]
    changed_upload=any(has_file and not matches[i] for i,has_file in enumerate(present))
    saved_widgets = await scope.locator(','.join(SAVED_RESUME_CARDS.values())).count()
    for proof in proofs:
        if proof['kind'] == 'upload':
            valid = matching_upload if inputs else (
                (await proof['element'].evaluate('el => el.isConnected') or proof.get('transferred', False))
                and await upload_matches(proof['element'], resume))
            if valid:
                return True
            continue
        try:
            if changed_upload:
                continue
            link, radio = proof['link'], proof['radio']
            current_widgets = bool(saved_widgets or inputs)
            valid = await link.evaluate('(el, expected) => el.ownerDocument.defaultView?.document === el.ownerDocument && el.href === expected.url && (el.isConnected || expected.transferred && !expected.connected)',
                {'url': proof['url'], 'connected': current_widgets, 'transferred': proof.get('transferred', False)})
            if valid and radio is not None:
                valid = await radio.evaluate("(el, expected) => (el.isConnected || expected.transferred && !expected.connected) && (el.checked || el.getAttribute('aria-checked') === 'true')",
                    {'connected': current_widgets, 'transferred': proof.get('transferred', False)})
            if valid:
                frame = await link.owner_frame()
                data = await download(proof['url'], frame) if frame is not None else None
                if data is not None and resume.sha256 and hashlib.sha256(data).hexdigest() == resume.sha256:
                    return True
        except Exception:
            pass
    return False
