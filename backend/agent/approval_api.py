"""Paused, idle-only review endpoints for immutable application approvals."""
from fastapi import HTTPException
from pydantic import BaseModel, Field

from .approvals import ApprovalStore, build_snapshot


class ApprovalRequest(BaseModel):
    token: str = Field(min_length=1,max_length=128)
    confirmation: bool = False


def register_approval_api(app, store, worker, runtime):
    approvals=ApprovalStore(store,runtime)
    app.state.approvals=approvals

    def require_idle():
        if worker.busy or worker.run_lock.locked() or not store.config().settings.paused:
            raise HTTPException(409,'Pause the agent and wait for current work before reviewing applications.')

    @app.get('/api/jobs/{id}/review')
    async def review(id: str):
        require_idle()
        async with worker.run_lock:
            try:
                with store.connection() as db:
                    job,config,resume,documents=approvals._context(db,id)
                    approvals._reviewable(db,job)
                    latest=approvals._latest(db,id)
                    current=build_snapshot(job,config,resume,documents)
                    generation=store._resume_generation(db)
                    document_generation=approvals._document_generation(db)
                if (latest and (latest['job_version'] == job.updated_at or latest.get('approved') and latest.get('approved_job_version') == job.updated_at)
                        and latest.get('resume_generation') == generation
                        and latest.get('document_generation') == document_generation
                        and latest['payload'] == current['payload'] and latest['token'] == current['token']):
                    return latest
                return approvals.create(id)
            except KeyError as exc:
                raise HTTPException(404,'Job not found') from exc
            except (ValueError,OSError,ImportError) as exc:
                raise HTTPException(409,str(exc)) from exc

    @app.post('/api/jobs/{id}/review')
    async def approve(id: str, body: ApprovalRequest):
        require_idle()
        async with worker.run_lock:
            try:
                return approvals.approve(id,body.token,body.confirmation,paused_only=True)
            except KeyError as exc:
                raise HTTPException(404,'Job not found') from exc
            except (ValueError,OSError,ImportError) as exc:
                raise HTTPException(409,str(exc)) from exc

    return approvals
