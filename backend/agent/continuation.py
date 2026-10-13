"""In-memory continuation bound to the current application document and facts."""
import hashlib
import json
from dataclasses import dataclass, field
from uuid import uuid4

from .adapters import verified_scopes
from .forms import prefilled_candidates


def configuration_key(config):
    data = config.model_dump(mode='json', exclude={'revision'})
    data['settings'].pop('paused')
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@dataclass
class Continuation:
    job_id: str
    target: str
    config_key: str
    resume_id: str
    resume_hash: str
    document: str
    answers: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    uploaded: bool = False
    approved: dict = field(default_factory=dict)
    attachments: list = field(default_factory=list)

    @classmethod
    async def create(cls, page, job, config, resume):
        token = uuid4().hex
        await page.evaluate('(token) => document.__orbitApplicationDocument = token', token)
        return cls(job.id, job.url if job.easy_apply else job.application_url,
                   configuration_key(config), resume.id, resume.sha256, token)

    async def valid(self, page, job, config, resume):
        if page is None or page.is_closed():
            return False
        if (self.job_id, self.target, self.config_key, self.resume_id, self.resume_hash) != (
                job.id, job.url if job.easy_apply else job.application_url,
                configuration_key(config), resume.id, resume.sha256):
            return False
        try:
            return await page.evaluate('document.__orbitApplicationDocument') == self.document
        except Exception:
            return False

    async def snapshot(self, page, hosts, job, config, resume):
        if not await self.valid(page, job, config, resume):
            raise ValueError('The retained application changed. Prepare it again before continuing.')
        scopes = await verified_scopes(page, hosts, job.easy_apply)
        if not scopes:
            raise ValueError('The retained application form is no longer available.')
        _, scope, _ = scopes[0]
        fields = await prefilled_candidates(scope, config.profile, resume, job.country)
        payload = {'document': self.document, 'config': self.config_key,
                   'resume': self.resume_hash, 'fields': fields}
        token = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return {'token': token, 'fields': fields}
