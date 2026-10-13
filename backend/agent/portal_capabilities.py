"""Public support claims bounded by offline fixtures, never vendor certification."""
from copy import deepcopy


_NEW_PORTALS = ('workday', 'icims', 'taleo', 'smartrecruiters', 'ashby', 'bamboohr', 'successfactors')
_COMMON_LIMITATIONS = ['account_creation', 'captcha', 'mfa', 'custom_assessments', 'custom_or_changed_markup', 'unsupported_localized_action_labels']


def capability_registry() -> list[dict]:
    """Return independent JSON-safe records suitable for API and UI consumers."""
    entries = []
    for name in ('linkedin', 'greenhouse', 'lever', *_NEW_PORTALS, 'generic'):
        dedicated = name != 'generic'
        entries.append({
            'name': name,
            'adapter_version': '1',
            'status': 'fixture_verified' if dedicated else 'best_effort',
            'live_certified': False,
            'supported_scopes': (['semantic_application_fields', 'resume_upload', 'multi_step_review_submit',
                                  'explicit_submission_gate', 'visible_confirmation'] if dedicated else ['semantic_html_form']),
            'limitations': list(_COMMON_LIMITATIONS),
            'evidence': 'Offline synthetic semantic DOM fixtures; no live employer application certification.',
        })
    return deepcopy(entries)
