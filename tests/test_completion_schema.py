import pytest
from pydantic import ValidationError

from agent.models import Config, Job, Profile, Resume, Settings


def test_new_configuration_preserves_legacy_defaults():
    config = Config.model_validate({"profile": {}, "settings": {}, "searches": []})
    assert config.settings.max_search_pages == 20
    assert config.settings.registration_hosts == []
    assert config.settings.sso_hosts == {}
    assert config.profile.willing_to_relocate is None
    assert config.settings.prevent_sleep is False
    assert Config.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize("field,value", [("max_search_pages", 0), ("retry_attempts", 11), ("retry_backoff_seconds", -1)])
def test_invalid_operating_limits_are_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_audit_and_cv_conflict_state_survive_serialization():
    resume = Resume(label="CV", identity_facts={"email": "jhon@example.com"}, conflicts=["Email differs"], conflict_acknowledged=True)
    job = Job(linkedin_id="1", title="Engineer", company="Example", adapter_name="lever", adapter_version="1", retry_at="2026-10-13T00:00:00+00:00")
    assert Resume.model_validate_json(resume.model_dump_json()).conflict_acknowledged
    assert Job.model_validate_json(job.model_dump_json()).adapter_name == "lever"


def test_host_scopes_reject_urls_and_private_hosts():
    with pytest.raises(ValidationError):
        Settings(registration_hosts=["https://careers.example/path"])
    with pytest.raises(ValidationError):
        Settings(sso_hosts={"careers.example": ["127.0.0.1"]})
