"""Grounded written drafts and sentence-safe narrative budgets."""
import copy
import json
from types import SimpleNamespace

import pytest

from agent.models import Answer, Job, Profile, Resume
from agent.profile_records import ApplicantRecords


def applicant(**changes):
    records = changes.pop('records', {})
    data = Profile(**changes).model_dump()
    data['records'] = ApplicantRecords.model_validate(records)
    return SimpleNamespace(**data)


def role(title='Registered Nurse', description='Ward care and nursing license'):
    return Job(linkedin_id='1', title=title, company='City Hospital', description=description)


def cv(**changes):
    return Resume(**({'label': 'Original CV', 'id': 'cv-original', 'confirmed': True,
                      'sha256': 'original-hash', 'text': 'Supported inpatient ward care.'} | changes))


def test_nursing_cover_letter_copies_known_facts_without_inventing_requirement():
    from agent.written_documents import draft_document
    p = applicant(summary='I provide patient care.', records={
        'employment': [{'id': 'ward', 'title': 'Registered Nurse', 'organization': 'County Hospital', 'description': 'Provided ward care.'}],
        'licenses': [{'id': 'rn', 'name': 'Nursing license', 'identifier': 'RN-42'}],
    })
    job = role(description='Requires 10 years, ICU leadership and an advanced nursing license.')
    draft = draft_document('cover_letter', job, p, cv())
    json.dumps(draft)
    assert draft['kind'] == 'cover_letter' and draft['reviewed'] is False
    assert draft['job_id'] == job.id and draft['resume_id'] == 'cv-original'
    assert draft['resume_hash'] == 'original-hash'
    assert 'Registered Nurse' in draft['text'] and 'City Hospital' in draft['text']
    assert 'Provided ward care.' in draft['text'] and 'RN-42' in draft['text']
    assert '10 years' not in draft['text'] and 'ICU' not in draft['text'] and 'advanced' not in draft['text']
    assert 'profile:records:licenses:rn:identifier' in draft['sources']
    assert any(fact['source'] == 'profile:records:employment:ward:description' for fact in draft['facts'])
    assert draft['id'] and draft['created_at']


def test_driving_tailoring_reorders_relevant_record_and_keeps_original_immutable():
    from agent.written_documents import draft_document
    p = applicant(summary='I work carefully.', records={
        'employment': [{'id': 'retail', 'title': 'Retail assistant', 'description': 'Arranged shop displays.'},
                       {'id': 'driver', 'title': 'Delivery driver', 'description': 'Delivered parcels using route plans.'}],
        'licenses': [{'id': 'cdl', 'name': 'Commercial Driver License', 'issuer': 'State DMV', 'identifier': 'CDL-123'}],
    })
    original = cv(text='Experience: Delivered parcels in vans.\nExperience: Arranged shop displays.')
    before_profile, before_resume = copy.deepcopy(vars(p)), original.model_dump()
    draft = draft_document('cv_tailoring', role('Delivery Driver', 'Delivery routes and Commercial Driver License'), p, original)
    assert 'Delivered parcels using route plans.' in draft['text']
    assert 'Commercial Driver License' in draft['text'] and 'CDL-123' in draft['text']
    assert 'Arranged shop displays.' not in draft['text']
    assert draft['reviewed'] is False
    assert original.model_dump() == before_resume
    assert vars(p) == before_profile
    assert draft['resume_id'] == original.id


@pytest.mark.parametrize('changes', [{'confirmed': False}, {'conflicts': ['email conflict'], 'conflict_acknowledged': False}])
def test_unconfirmed_or_unacknowledged_cv_claims_are_excluded(changes):
    from agent.written_documents import draft_document
    draft = draft_document('cover_letter', role(), applicant(summary='I provide patient care.'), cv(text='Managed 50 people.', **changes))
    assert '50 people' not in draft['text']
    assert not any(source.startswith('resume:') for source in draft['sources'])


def test_confirmed_cv_claims_are_attributed_and_contact_lines_excluded():
    from agent.written_documents import draft_document
    original = cv(text='Full name: Another Person\nEmail: private@example.com\nSupported inpatient ward care.\nManaged 5 clinic rooms.')
    draft = draft_document('cover_letter', role(), applicant(), original)
    assert 'Supported inpatient ward care.' in draft['text'] and 'Managed 5 clinic rooms.' in draft['text']
    assert 'Another Person' not in draft['text'] and 'private@example.com' not in draft['text']
    assert 'resume:cv-original:2' in draft['sources']
    assert 'identity' not in draft


def test_unknown_facts_are_not_invented_and_empty_sources_require_input():
    from agent.written_documents import draft_document
    with pytest.raises(ValueError, match='confirmed'):
        draft_document('cover_letter', role(description='Needs a PhD, HGV license and 12 years.'), applicant(), cv(confirmed=False))


@pytest.mark.parametrize('changed', ['profile', 'cv_text', 'cv_hash', 'cv_confirmation', 'job'])
def test_source_fingerprint_detects_stale_review_dependencies(changed):
    from agent.written_documents import draft_document, document_is_current
    p, job, resume = applicant(summary='I provide patient care.'), role(), cv()
    draft = draft_document('cover_letter', job, p, resume)
    assert document_is_current(draft, job, p, resume)
    if changed == 'profile':
        p.summary = 'I provide outpatient care.'
    elif changed == 'cv_text':
        resume.text = 'Managed 5 clinic rooms.'
    elif changed == 'cv_hash':
        resume.sha256 = 'changed-hash'
    elif changed == 'cv_confirmation':
        resume.confirmed = False
    else:
        job.description = 'Changed role requirements.'
    assert not document_is_current(draft, job, p, resume)


def test_draft_budget_uses_complete_facts_and_no_midclaim_cut():
    from agent.written_documents import draft_document
    p = applicant(summary='I do not hold an HGV license.\nI have driven delivery vans for 2.5 years.\n' + 'Extensive supported background ' * 100)
    draft = draft_document('cover_letter', role('Van Driver', 'Delivery vans'), p, cv(confirmed=False), max_length=300)
    assert len(draft['text']) <= 300
    for fact in draft['facts']:
        assert fact['text'] in draft['text']
    assert 'I do not hold an HGV license.' in draft['text']
    assert 'I have driven delivery vans for 2.5 years.' in draft['text']
    assert 'Extensive supported background' not in draft['text']


@pytest.mark.parametrize('value,budget,want', [
    ('I do not hold an HGV license. I drive vans.', 30, 'I do not hold an HGV license.'),
    ('I drove 2.5 tonne vans. I supervised deliveries.', 24, 'I drove 2.5 tonne vans.'),
    ('I have not driven HGV vehicles', 15, None),
    ('I trained with Dr. Jones in ward care. I support patients.', 20, None),
    ('I provided care; not independently.', 18, None),
    ('I drive vans.', 0, None),
])
def test_fit_narrative_never_truncates_numbers_negation_or_abbreviations(value, budget, want):
    from agent.written_documents import fit_narrative
    answer = Answer(value=value, source='confirmed_evidence:profile:summary,resume:cv-original:2')
    result = fit_narrative(answer, budget)
    if want is None:
        assert result is None
    else:
        assert result.value == want
        assert result.source == answer.source
        assert len(result.value) <= budget
    assert answer.value == value


@pytest.mark.parametrize('kind,budget', [('invented', 4000), ('cover_letter', 0), ('cover_letter', 1)])
def test_invalid_kind_or_impossible_budget_fails_explicitly(kind, budget):
    from agent.written_documents import draft_document
    with pytest.raises(ValueError):
        draft_document(kind, role(), applicant(summary='I provide care.'), cv(), max_length=budget)


def test_fit_narrative_does_not_split_initialisms_or_lowercase_continuations():
    from agent.written_documents import fit_narrative
    for text in ('I worked in the U.S. Army clinic for 2.5 years. I support patients.',
                 'I trained at Example Ltd. for 5 years. I support patients.'):
        assert fit_narrative(Answer(value=text, source='profile:summary'), 26) is None


def test_role_context_cannot_insert_an_unattributed_applicant_sentence():
    from agent.written_documents import draft_document
    job = role('Nurse.\nI hold a PhD', 'Requires ICU certification')
    draft = draft_document('cover_letter', job, applicant(summary='I provide care.'), cv(confirmed=False))
    assert '\nI hold a PhD' not in draft['text']
    assert 'Role: "Nurse. I hold a PhD"' in draft['text']
    assert 'ICU certification' not in draft['text']
    assert [fact['text'] for fact in draft['facts']] == ['I provide care.']
