"""Local, explicitly reviewable drafts made only from supplied applicant facts.

This module never edits a CV, selects an attachment, calls a model, writes a
file, or marks a draft reviewed. Consumers must check the source fingerprint
again before reviewing or exporting a retained draft.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from uuid import uuid4

from .evidence import cv_claim, redact_evidence
from .models import Answer, now
from .profile_records import profile_facts


_ABBREVIATIONS = {'dr.', 'mr.', 'mrs.', 'ms.', 'prof.', 'sr.', 'jr.', 'st.', 'vs.', 'etc.', 'e.g.', 'i.e.', 'no.', 'ltd.', 'inc.', 'corp.'}
_STOP_WORDS = {'a', 'an', 'and', 'at', 'for', 'from', 'in', 'is', 'it', 'of', 'on', 'or', 'the', 'to', 'with', 'you', 'your', 'our', 'we', 'role', 'required', 'requires'}


def _sentences(value: str) -> list[str]:
    """Split terminal punctuation only, retaining decimals and abbreviations.

    A semicolon or wrapped line is not evidence that a claim is complete. The
    conservative fallback keeps such material together, rather than clipping it.
    """
    pieces, start = [], 0
    for boundary in re.finditer(r'[.!?](?:["\u201d\u2019])?\s+', value):
        before = value[start:boundary.end()].rstrip()
        last_word = before.split()[-1].casefold().rstrip('"\u201d\u2019') if before else ''
        if (last_word in _ABBREVIATIONS or re.fullmatch(r'(?:[a-z]+\.){2,}|[a-z]\.', last_word)
                or value[boundary.end():boundary.end() + 1].islower()):
            continue
        pieces.append(value[start:boundary.end()].strip())
        start = boundary.end()
    if value[start:].strip():
        pieces.append(value[start:].strip())
    return pieces


def fit_narrative(answer: Answer, max_length: int) -> Answer | None:
    """Fit a prefix of complete supported sentences; never slice a claim.

    If the first complete unit does not fit, return None. Retaining a prefix
    avoids dropping a negation or qualification while keeping a later claim.
    """
    if max_length <= 0 or not answer.value.strip():
        return None
    value = answer.value.strip()
    if len(value) <= max_length:
        return Answer(value=value, source=answer.source)
    selected = []
    for sentence in _sentences(value):
        candidate = ' '.join([*selected, sentence])
        if len(candidate) > max_length:
            break
        selected.append(sentence)
    return Answer(value=' '.join(selected), source=answer.source) if selected else None


def _tokens(text: str) -> set[str]:
    return {word.removesuffix('s') if len(word) > 3 else word
            for word in re.findall(r'[a-z0-9]+', text.casefold()) if word not in _STOP_WORDS}


def _facts(profile, resume) -> list[dict]:
    facts = []
    # Profile values are directly user supplied; there is no separate profile
    # confirmation flag. The CV does have an explicit confirmation gate.
    for field in ('summary', 'work_history', 'education'):
        for index, line in enumerate(getattr(profile, field, '').splitlines()):
            text = redact_evidence(line, profile, resume)
            if text:
                facts.append({'source': f'profile:{field}:{index}', 'text': text,
                              'group': f'profile:{field}:{index}'})
    section_names = {'employment': 'Employment', 'education': 'Education',
                     'licenses': 'License', 'certifications': 'Certification',
                     'languages': 'Language', 'address': 'Address',
                     'preferences': 'Work preference', 'salary': 'Salary', 'availability': 'Availability'}
    for source, value in profile_facts(profile):
        parts = source.split(':')
        label = section_names.get(parts[2], parts[2].replace('_', ' ').title())
        field = parts[-1].replace('_', ' ')
        facts.append({'source': source, 'text': f'{label} {field}: {value}', 'group': source.rsplit(':', 1)[0]})
    if resume is not None and resume.confirmed and (not resume.conflicts or resume.conflict_acknowledged):
        for index, line in enumerate(resume.text.splitlines()):
            text = redact_evidence(line, profile, resume)
            if text and cv_claim(text) and len(text.split()) >= 3 and len(text) >= 15:
                source = f'resume:{resume.id}:{index}'
                facts.append({'source': source, 'text': text, 'group': source})
    return facts


def _profile_snapshot(profile):
    if hasattr(profile, 'model_dump'):
        data = profile.model_dump(mode='json')
    else:
        data = dict(vars(profile))
    # Include structured facts even for a legacy consumer whose model dump
    # excludes a separately attached records object.
    data['structured_facts'] = profile_facts(profile)
    return data


def document_fingerprint(job, profile, resume) -> str:
    """Hash all source dependencies, including CV confirmation and source text.

    Reading a source file is deliberately outside this pure helper. The caller
    remains responsible for checking the selected CV's actual attachment hash.
    """
    payload = {
        'job': {field: getattr(job, field, '') for field in ('id', 'title', 'company', 'description')},
        'profile': _profile_snapshot(profile),
        'resume': (resume.model_dump(mode='json') if resume is not None else None),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=lambda value: value.model_dump(mode='json') if hasattr(value, 'model_dump') else str(value))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def document_is_current(draft: dict, job, profile, resume) -> bool:
    return bool(draft.get('source_fingerprint')) and draft['source_fingerprint'] == document_fingerprint(job, profile, resume)


def draft_document(kind: str, job, profile, resume, *, max_length: int = 4000) -> dict:
    """Create an unreviewed draft by copying/reordering confirmed source facts.

    Raises ValueError for an unsupported kind, no usable facts, or a budget
    that cannot hold the contextual heading and at least one complete fact.
    Role requirements rank facts; their text never becomes an applicant claim.
    """
    if kind not in {'cover_letter', 'cv_tailoring'}:
        raise ValueError('Unsupported written document kind')
    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length <= 0:
        raise ValueError('Document length budget must be a positive integer')
    facts = _facts(profile, resume)
    if not facts:
        raise ValueError('Add confirmed profile or CV facts before drafting')
    terms = _tokens(job.title + ' ' + job.description)
    group_text = defaultdict(str)
    for fact in facts:
        group_text[fact['group']] += ' ' + fact['text']
    relevance = {group: len(_tokens(text) & terms) for group, text in group_text.items()}
    ranked = sorted(facts, key=lambda fact: -relevance[fact['group']])
    if kind == 'cv_tailoring' and any(relevance.values()):
        ranked = [fact for fact in ranked if relevance[fact['group']] > 0]
    # Context is attributed and quoted separately from applicant facts. Collapse
    # line breaks so external role text cannot inject a standalone claim.
    role = ' '.join(job.title.split())
    company = ' '.join(job.company.split())
    context = ' at '.join(value for value in (role, company) if value)
    context_lines = '\n'.join(label + ': ' + json.dumps(value, ensure_ascii=False)
                              for label, value in (('Role', role), ('Employer', company)) if value)
    if kind == 'cover_letter':
        title = 'Cover letter' + (f' — {context}' if context else '')
        header = 'Dear hiring team,\n\nPlease consider my application.\n' + (context_lines + '\n\n' if context_lines else '\n')
        footer = '\n\nThank you for your consideration.'
    else:
        title = 'CV tailoring draft' + (f' — {context}' if context else '')
        header = 'CV tailoring draft — review required\n' + (context_lines + '\n\n' if context_lines else '\n')
        footer = ''
    selected = []
    for fact in ranked:
        # Keep each selected source unit intact. A long unit is omitted rather
        # than shortened; facts and their displayed text remain one-to-one.
        candidate = header + '\n'.join(item['text'] for item in [*selected, fact]) + footer
        if len(candidate) <= max_length:
            selected.append(fact)
    if not selected:
        raise ValueError('No complete confirmed fact fits the document length budget')
    public_facts = [{'id': hashlib.sha256((fact['source'] + '\0' + fact['text']).encode()).hexdigest()[:16],
                     'source': fact['source'], 'text': fact['text']} for fact in selected]
    return {
        'id': uuid4().hex, 'kind': kind, 'job_id': job.id,
        'resume_id': resume.id if resume is not None else '',
        'resume_hash': resume.sha256 if resume is not None else '',
        'source_fingerprint': document_fingerprint(job, profile, resume),
        'title': title, 'text': header + '\n'.join(fact['text'] for fact in selected) + footer,
        'sources': list(dict.fromkeys(fact['source'] for fact in selected)),
        'facts': public_facts, 'reviewed': False, 'created_at': now(),
    }
