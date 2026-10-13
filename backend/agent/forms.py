from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
from collections import Counter
import re
from uuid import uuid4

from .answers import question_key, resolve_answer, narrative_question, demographic_question
from .models import Answer, Profile, Resume


@dataclass
class FormOutcome:
    missing: list[str] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    uploaded: bool = False
    field_details: list[dict] = field(default_factory=list)
    document_uploads: list[dict] = field(default_factory=list)


class OperationPaused(Exception):
    pass


async def act(can_act, operation):
    if not can_act():
        raise OperationPaused("Agent paused or configuration changed")
    return await operation()


def choose_option(value: str, options: list[str]) -> str | None:
    exact = [o for o in options if question_key(o) == question_key(value)]
    if len(exact) == 1:
        return exact[0]
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        number = float(value)
        matching = []
        for option in options:
            span = re.search(r"(\d+)\s*[-–]\s*(\d+)", option)
            plus = re.search(r"(\d+)\s*\+", option)
            if span and float(span[1]) <= number <= float(span[2]) or plus and number >= float(plus[1]):
                matching.append(option)
        if len(matching) == 1:
            return matching[0]
    return None


FIELD_INFO = """el => {
 const fieldset = el.closest('fieldset,[role=radiogroup]');
 const legend = fieldset?.querySelector('legend')?.innerText || fieldset?.getAttribute('aria-label');
 const labelled = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).map(id => el.ownerDocument.getElementById(id)?.innerText || '').join(' ').trim();
 const direct = [...(el.labels || [])].map(l => {
   const copy = l.cloneNode(true); copy.querySelectorAll('input,select,textarea').forEach(n => n.remove()); return copy.textContent;
 }).join(' ');
 const group = el.closest('[data-test-form-element],.jobs-easy-apply-form-element,.application-question,.field');
 const label = (((el.type || el.getAttribute('role')) === 'radio' ? legend || group?.querySelector('label')?.innerText : null)
   || el.getAttribute('aria-label') || labelled || direct || legend || group?.querySelector('label')?.innerText
   || el.getAttribute('placeholder') || el.name || el.id || '').replace(/\\*/g,'').trim();
 const record=el.closest('[data-section],fieldset');
 return {label, type:el.type || el.getAttribute('role') || el.tagName.toLowerCase(), role:el.getAttribute('role'), name:el.name || fieldset?.id || legend || '', context:record?.getAttribute('data-section') || record?.querySelector(':scope > legend')?.textContent || '',
 required:el.required || el.getAttribute('aria-required') === 'true' || /\\*/.test(direct || legend || ''),
 options:el.tagName === 'SELECT' ? [...el.options].map(o => o.textContent.trim()) : el.getAttribute('role') === 'listbox' ? [...el.querySelectorAll('[role=option]')].map(o=>o.textContent.trim()) : [],
 disabled:el.disabled || el.getAttribute('aria-disabled') === 'true', autocomplete:el.autocomplete,
 max_length:Number.isInteger(el.maxLength) && el.maxLength >= 0 ? el.maxLength : null,
 max_words:(label.match(/(?:max(?:imum)?(?: of)?\\s*(\\d+)\\s*words?|(\\d+)\\s*words?\\s*max(?:imum)?)/i)||[]).slice(1).find(Boolean)||null};
}"""


FIELDS = 'input:not([type=hidden]):not([type=submit]):not([type=button]),select,textarea,[role="combobox"]:not(input):not(select),[role="radio"]:not(input),[role="checkbox"]:not(input),[role="listbox"][aria-multiselectable=true]'
PERSONAL_ACTION = re.compile(r'\bsignature\b|\bsign here\b|\bi (?:certify|attest|declare|agree)\b|\bassessment\b|\bcoding test\b', re.I)


def is_resume_field(label):
    return bool(re.search(r'resume|curriculum|\bcv\b',label,re.I) and not re.search(r'passport|national id|identity|cover|photo|certificat|licen[cs]e|\bnot\s+(?:a\s+)?(?:cv|resume)\b',label,re.I))


async def field_key(element, info):
    identity = await element.evaluate('''(el, token) => {
      const root = el.ownerDocument;
      root.__orbitToken ||= token;
      el.__orbitToken ||= token;
      const group = el.closest('fieldset,[role=radiogroup]');
      if (group && (el.type === 'radio' || el.getAttribute('role') === 'radio')) {
        group.__orbitToken ||= token;
        return root.__orbitToken + ':' + group.__orbitToken;
      }
      return root.__orbitToken + ':' + el.__orbitToken;
    }''', uuid4().hex)
    return identity + ':' + await field_semantics(element, info)


async def field_semantics(element, info):
    choices = await element.evaluate('''el => {
      const group = el.closest('fieldset,[role=radiogroup]');
      return group && (el.type === 'radio' || el.getAttribute('role') === 'radio')
        ? [...group.querySelectorAll('input[type=radio],[role=radio]')].map(r => ({
            value:r.value, label:r.getAttribute('aria-label') || [...(r.labels||[])].map(l=>l.innerText).join(' ') || r.innerText})) : [];
    }''')
    semantics = hashlib.sha256(json.dumps([info, choices], sort_keys=True).encode()).hexdigest()
    return semantics


async def form_signature(scope):
    values = []
    fields = scope.locator(FIELDS)
    for index in range(await fields.count()):
        element = fields.nth(index)
        info = await element.evaluate(FIELD_INFO)
        if info['type'] == 'file' or await element.is_visible():
            values.append(await field_semantics(element, info))
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


async def semantic_counts(scope):
    fields=scope.locator(FIELDS)
    counts=Counter()
    seen_radios=set()
    for i in range(await fields.count()):
        element=fields.nth(i)
        info=await element.evaluate(FIELD_INFO)
        semantic=await field_semantics(element,info)
        if info['type']=='radio' and info['name']:
            identity=(semantic,info['name'],info.get('context',''))
            if identity in seen_radios:continue
            seen_radios.add(identity)
        counts[semantic]+=1
    return counts


async def missing_details(scope, questions):
    result, seen = [], set()
    counts=await semantic_counts(scope)
    fields = scope.locator(FIELDS)
    for index in range(await fields.count()):
        element = fields.nth(index)
        info = await element.evaluate(FIELD_INFO)
        if info['disabled'] or not await element.is_visible() or not any(q.startswith(info['label']) for q in questions):
            continue
        key = await field_semantics(element, info)
        if key in seen:
            continue
        seen.add(key)
        options = await element.evaluate('''el => {
          if(el.tagName === 'SELECT') return [...el.options].filter(o=>o.value && !o.disabled).map(o=>o.textContent.trim());
          const group=el.closest('fieldset,[role=radiogroup]');
          return group ? [...group.querySelectorAll('input[type=radio],[role=radio]')].map(r=>r.getAttribute('aria-label') || [...(r.labels||[])].map(l=>l.innerText).join(' ') || r.innerText) : [];
        }''')
        constraints = await element.evaluate("el => ({min:el.getAttribute('min'),max:el.getAttribute('max'),pattern:el.getAttribute('pattern'),max_length:el.maxLength > 0 ? el.maxLength : null})")
        result.append({'key':key, 'label':info['label'], 'type':info['type'], 'required':info['required'], 'ambiguous':counts[key]>1,
            'options':options, 'value':await existing_value(element, info, scope) if info['type'] not in {'password','file'} else '',
            'reason':next(q for q in questions if q.startswith(info['label'])), 'personal_action':bool(PERSONAL_ACTION.search(question_key(info['label']))), **constraints})
    return result


async def existing_value(element, info, scope):
    kind = info['type']
    if kind == 'radio':
        radios = scope.locator('input[type=radio],[role=radio]')
        for index in range(await radios.count()):
            candidate = radios.nth(index)
            other = await candidate.evaluate(FIELD_INFO)
            if other['name'] == info['name'] and await candidate.evaluate("el => el.checked || el.getAttribute('aria-checked') === 'true'"):
                return await candidate.evaluate("el => el.getAttribute('aria-label') || [...(el.labels||[])].map(l=>l.innerText).join(' ').trim() || el.innerText")
        return ''
    if kind == 'checkbox':
        return 'Yes' if await element.evaluate("el => el.checked || el.getAttribute('aria-checked') === 'true'") else 'No'
    return await element.evaluate("el => el.tagName === 'SELECT' ? (el.multiple ? (el.selectedOptions.length ? JSON.stringify([...el.selectedOptions].filter(o=>o.value).map(o=>o.textContent.trim())) : '') : [...el.selectedOptions].filter(o=>o.value).map(o=>o.textContent.trim()).join(', ')) : el.getAttribute('role') === 'listbox' ? (el.querySelectorAll('[role=option][aria-selected=true]').length ? JSON.stringify([...el.querySelectorAll('[role=option][aria-selected=true]')].map(o=>o.textContent.trim())) : '') : (el.value ?? el.textContent ?? '').trim()")


async def prefilled_candidates(scope, profile, resume, country):
    """Expose only supported required values that still need user confirmation."""
    result, seen = [], set()
    fields = scope.locator(FIELDS)
    for index in range(await fields.count()):
        element = fields.nth(index)
        info = await element.evaluate(FIELD_INFO)
        if not info['required'] or info['disabled'] or not await element.is_visible() or info['type'] in {'password', 'file'}:
            continue
        if PERSONAL_ACTION.search(question_key(info['label'])) or resolve_answer(info['label'], profile, resume, country) is not None:
            continue
        value = await existing_value(element, info, scope)
        valid = await element.evaluate('el => el.checkValidity ? el.checkValidity() : true')
        if not value or not valid:
            continue
        key = await field_key(element, info)
        if key not in seen:
            result.append({'key': key, 'semantic_key': await field_semantics(element, info), 'label': info['label'], 'value': value})
            seen.add(key)
    counts=await semantic_counts(scope)
    for item in result:item['restorable']=counts[item['semantic_key']]==1
    return result


async def fill_form(scope, profile: Profile, resume: Resume, country: str = "", narrator=None, can_act=lambda: True, *, approved_prefilled=None, attachment_verified=False, confirmed_fields=None, documents=None, saved_attachment_verified=False) -> FormOutcome:
    result = FormOutcome()
    from .complex_forms import ensure_repeating_groups, record_context, contextual_answer
    await ensure_repeating_groups(scope,profile,can_act)
    fields = scope.locator(FIELDS)
    # This root stays in the field's document, including when inside an iframe.
    document_scope = scope.locator("xpath=ancestor-or-self::*[last()]")
    radio_done = set()
    counts=await semantic_counts(scope)
    field_count=await fields.count()
    processed=set()
    for _ in range(200):
        element=None
        for i in range(await fields.count()):
            candidate=fields.nth(i)
            candidate_info=await candidate.evaluate(FIELD_INFO)
            identity=await field_key(candidate,candidate_info)
            if identity not in processed:
                processed.add(identity)
                element,info=candidate,candidate_info
                break
        if element is None:break
        if field_count!=await fields.count():
            counts=await semantic_counts(scope)
            field_count=await fields.count()
        if not can_act():
            result.missing.append("Agent paused or configuration changed")
            break
        label, kind = info["label"], info["type"]
        context=await record_context(scope,element,profile)
        answer_label=f'{context[0].title()} {context[1]+1} · {label}' if context is not None else label
        if info["disabled"] or (kind != "file" and not await element.is_visible()):
            continue
        if kind == "file":
            if is_resume_field(label):
                if attachment_verified:
                    from .attachments import upload_matches
                    handle=await element.element_handle()
                    if await upload_matches(handle,resume) or saved_attachment_verified and not await element.evaluate('el=>!!el.files?.length'):
                        result.uploaded = True
                        continue
                if not resume.path or not Path(resume.path).is_file():
                    result.missing.append("Selected CV file is missing")
                    continue
                original_name = resume.filename or Path(resume.path).name
                mime = "application/pdf" if original_name.lower().endswith(".pdf") else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                await act(can_act, lambda: element.set_input_files({"name": original_name, "mimeType": mime, "buffer": Path(resume.path).read_bytes()}))
                if original_name not in await element.input_value():
                    result.missing.append("CV upload could not be verified")
                else:
                    result.uploaded = True
            else:
                from .document_library import document_for_field,verified_document_path
                document=document_for_field(label,documents or [])
                if document is not None:
                    from .attachments import upload_matches
                    path=verified_document_path(document)
                    mime='application/pdf' if document.filename.lower().endswith('.pdf') else 'text/plain' if document.filename.lower().endswith('.txt') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                    await act(can_act,lambda:element.set_input_files({'name':document.filename,'mimeType':mime,'buffer':path.read_bytes()}))
                    handle=await element.element_handle()
                    if await upload_matches(handle,document):
                        result.document_uploads.append({'element':handle,'document':document,'label':answer_label,'identity':await field_key(element,info)})
                        result.answers[answer_label]=document.filename
                        result.sources[answer_label]='document:'+document.id+':'+document.sha256
                    else:result.missing.append(label+' (document upload could not be verified)')
                elif info['required']:result.missing.append(label or 'Unsupported attachment')
            continue
        if kind == "password":
            result.missing.append("Portal login required")
            continue
        answer = contextual_answer(context,label) if context is not None else resolve_answer(label, profile, resume, country)
        current = await existing_value(element, info, scope)
        key = await field_key(element, info)
        semantic_key=await field_semantics(element,info)
        confirmed = (confirmed_fields or {}).get(semantic_key) if counts[semantic_key]==1 else None
        if answer is None and confirmed and not PERSONAL_ACTION.search(question_key(label)) and (not current or current == confirmed):
            answer = Answer(value=confirmed, source='user_confirmed_current_field')
        if answer is None and not PERSONAL_ACTION.search(question_key(label)) and current and (approved_prefilled or {}).get(key) == current:
            answer = Answer(value=current, source='user_confirmed_current_field')
        if answer is None and profile.demographic_preference == "decline" and demographic_question(label):
            options = info["options"]
            if kind == "radio" and info["name"]:
                options = []
                radios = scope.locator('input[type="radio"]')
                for index in range(await radios.count()):
                    radio = radios.nth(index)
                    if await radio.get_attribute("name") == info["name"]:
                        options.append(await radio.evaluate("el => [...(el.labels||[])].map(l=>l.innerText).join(' ').trim()"))
            declines = [option for option in options if re.search(r"prefer not to (?:answer|say|disclose)|do not wish to (?:answer|disclose)|decline to (?:answer|self.identify)|choose not to (?:answer|disclose)", option, re.I)]
            if len(declines) == 1:
                answer = Answer(value=declines[0], source="profile:demographic_preference")
        if answer is None and narrator and narrative_question(label):
            answer = await narrator(label)
        if answer is None:
            if info["required"]:
                result.missing.append(label or "Unlabelled required field")
            continue
        value = answer.value
        limit=info.get('max_length')
        words=list(re.finditer(r'\S+',value))
        word_limit=int(info.get('max_words') or 0)
        if word_limit and len(words)>word_limit:limit=min(limit if limit is not None else len(value),words[word_limit-1].end())
        if limit is not None and len(value)>limit:
            if narrative_question(label):
                from .written_documents import fit_narrative
                fitted=fit_narrative(answer,limit)
                if fitted is not None:value=fitted.value
                else:
                    result.missing.append(label+' (confirmed evidence does not fit the length limit)')
                    continue
            else:
                result.missing.append(label+' (confirmed answer exceeds the length limit)')
                continue
        try:
            if current and kind!='select-multiple' and info['role']!='listbox' and current.strip().casefold() == value.strip().casefold() and await element.evaluate('el => el.checkValidity ? el.checkValidity() : true'):
                result.answers[answer_label], result.sources[answer_label] = current, answer.source
                continue
            if kind == 'select-multiple' or info['role'] == 'listbox':
                whole=choose_option(value,info['options'])
                try:typed=json.loads(value)
                except (ValueError,TypeError):typed=None
                values=typed if isinstance(typed,list) and all(isinstance(v,str) for v in typed) else [whole] if whole is not None else list(dict.fromkeys(v.strip() for v in value.split(',') if v.strip()))
                choices=[choose_option(v,info['options']) for v in values]
                if not choices or None in choices:
                    result.missing.append(label+' (no matching multiselect options)')
                    continue
                if kind == 'select-multiple':
                    await act(can_act,lambda:element.select_option(label=choices))
                else:
                    options=element.get_by_role('option')
                    for n,text in enumerate(await options.all_text_contents()):
                        option=options.nth(n)
                        if (await option.get_attribute('aria-selected')=='true') != (text.strip() in choices):
                            await act(can_act,option.click)
                actual=await element.evaluate("el=>el.tagName==='SELECT'?[...el.selectedOptions].map(o=>o.textContent.trim()):[...el.querySelectorAll('[role=option][aria-selected=true]')].map(o=>o.textContent.trim())")
                if set(actual) != set(choices):
                    result.missing.append(label+' (multiselect could not be verified)')
                    continue
            elif kind.startswith("select") or info["options"]:
                choice = choose_option(value, info["options"])
                if choice is None:
                    result.missing.append(label + " (no matching option)")
                    continue
                await act(can_act, lambda: element.select_option(label=choice))
            elif info["role"] == "combobox":
                tag = await element.evaluate('el => el.tagName.toLowerCase()')
                if tag in {"input", "textarea"}:
                    await act(can_act, lambda: element.fill(value))
                    await act(can_act, lambda: element.press("ArrowDown"))
                else:
                    await act(can_act, lambda: element.click())
                options = document_scope.get_by_role("option")
                controlled = await element.get_attribute('aria-controls') or await element.get_attribute('aria-owns')
                if controlled:
                    owned = document_scope.locator('[id=' + repr(controlled) + ']')
                    if await owned.count():
                        options = owned.get_by_role('option')
                labels = [(text or '').strip() for text in await options.all_text_contents()]
                choice = choose_option(value, labels)
                if choice is None:
                    result.missing.append(label + " (autocomplete selection unresolved)")
                    continue
                matched = [options.nth(n) for n, text in enumerate(labels) if text == choice]
                await act(can_act, lambda: matched[0].click())
            elif kind == "radio":
                name = info["name"]
                if not name or name in radio_done:
                    continue
                radio_done.add(name)
                group = scope.locator('input[type="radio"],[role="radio"]:not(input)')
                matched = []
                for n in range(await group.count()):
                    radio = group.nth(n)
                    if (await radio.evaluate(FIELD_INFO))["name"] != name:
                        continue
                    text = await radio.evaluate("el => el.getAttribute('aria-label') || [...(el.labels||[])].map(l=>l.innerText).join(' ').trim() || el.innerText")
                    if question_key(text) == question_key(value):
                        matched.append(radio)
                if len(matched) != 1:
                    result.missing.append(label + " (no matching choice)")
                    continue
                if await matched[0].get_attribute('role') == 'radio':
                    await act(can_act, lambda: matched[0].click())
                    if await matched[0].get_attribute('aria-checked') != 'true':
                        result.missing.append(label + ' (choice could not be verified)')
                        continue
                else:
                    await act(can_act, lambda: matched[0].check())
            elif kind == "checkbox":
                if question_key(value) not in {"yes", "no", "true", "false"}:
                    result.missing.append(label + " (requires explicit Yes/No answer)")
                    continue
                checked = question_key(value) in {"yes", "true"}
                if info['role'] == 'checkbox':
                    if (await element.get_attribute('aria-checked') == 'true') != checked:
                        await act(can_act, lambda: element.click())
                    if (await element.get_attribute('aria-checked') == 'true') != checked:
                        result.missing.append(label + ' (checkbox could not be verified)')
                        continue
                else:
                    await act(can_act, lambda: element.set_checked(checked))
            else:
                if kind=='month' and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):value=value[:7]
                await act(can_act, lambda: element.fill(value))
                if await existing_value(element,info,scope)!=value.strip():
                    result.missing.append(label+' (portal changed the supplied answer)')
                    continue
                valid = await element.evaluate("el => el.checkValidity ? el.checkValidity() : true")
                if not valid:
                    result.missing.append(label + " (answer does not satisfy field validation)")
                    continue
            result.answers[answer_label] = value
            result.sources[answer_label] = answer.source
        except OperationPaused:
            result.missing.append("Agent paused or configuration changed")
            break
        except Exception:
            result.missing.append(label + " (unsupported field interaction)")
    for i in range(await fields.count()):
        element=fields.nth(i)
        info=await element.evaluate(FIELD_INFO)
        if info['required'] and not info['disabled'] and await element.is_visible() and info['type']!='file':
            if not await element.evaluate('el=>el.checkValidity ? el.checkValidity() : true'):
                result.missing.append(info['label'] or 'Unlabelled required field')
    if await fields.count()>200:result.missing.append('Field inspection limit reached; review remaining fields manually')
    result.missing = list(dict.fromkeys(result.missing))
    result.field_details = await missing_details(scope, result.missing)
    return result
