import asyncio
import json
from collections.abc import AsyncIterator, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.orchestrator import AnalysisOrchestrator
from app.api.dependencies import get_session
from app.core.errors import LLMError
from app.models import RuntimeEvent
from app.schemas.analysis import AnalysisResumeRequest, AnalysisRunRead, AnalysisStartRequest
from app.services.analysis_runs import TERMINAL_STATUSES, AnalysisRunService
from app.services.projects import ProjectService
from app.services.workspace import WorkspaceService

router = APIRouter(tags=["analysis"])
SessionDependency = Annotated[Session, Depends(get_session)]


def build_orchestrator(request: Request) -> AnalysisOrchestrator:
    provider = request.app.state.llm_provider
    if provider is None:
        raise LLMError("LLM provider is not configured", "llm_not_configured", 503)
    return AnalysisOrchestrator(
        request.app.state.database,
        request.app.state.settings,
        provider,
        request.app.state.sandbox_executor,
    )


def schedule(request: Request, run_id: str, operation: Coroutine[Any, Any, None]) -> None:
    existing = request.app.state.analysis_background_tasks.get(run_id)
    if existing and not existing.done():
        operation.close()
        return
    task = asyncio.create_task(operation)
    request.app.state.analysis_background_tasks[run_id] = task
    task.add_done_callback(lambda _: request.app.state.analysis_background_tasks.pop(run_id, None))


@router.get("/projects/{project_id}/analysis", response_model=AnalysisRunRead | None)
def latest_analysis(
    project_id: str, request: Request, session: SessionDependency
) -> AnalysisRunRead | None:
    settings = request.app.state.settings
    ProjectService(session, WorkspaceService(settings.workspace_root)).get(project_id)
    run = AnalysisRunService(session).latest_for_project(project_id)
    return AnalysisRunRead.model_validate(run) if run is not None else None


@router.post("/projects/{project_id}/analysis", response_model=AnalysisRunRead, status_code=202)
async def start_analysis(
    project_id: str,
    payload: AnalysisStartRequest,
    request: Request,
    session: SessionDependency,
) -> AnalysisRunRead:
    settings = request.app.state.settings
    ProjectService(session, WorkspaceService(settings.workspace_root)).get(project_id)
    runner = build_orchestrator(request)
    run, created = AnalysisRunService(session).create(project_id, payload.message)
    session.commit()
    if created:
        schedule(request, run.id, runner.run(run.id))
    return AnalysisRunRead.model_validate(run)


@router.get("/analysis/{run_id}", response_model=AnalysisRunRead)
def get_analysis(run_id: str, session: SessionDependency) -> AnalysisRunRead:
    return AnalysisRunRead.model_validate(AnalysisRunService(session).get(run_id))


@router.post("/analysis/{run_id}/resume", response_model=AnalysisRunRead, status_code=202)
async def resume_analysis(
    run_id: str,
    payload: AnalysisResumeRequest,
    request: Request,
    session: SessionDependency,
) -> AnalysisRunRead:
    runner = build_orchestrator(request)
    run = AnalysisRunService(session).resume(run_id, payload.message)
    session.commit()
    schedule(request, run_id, runner.run(run_id))
    return AnalysisRunRead.model_validate(run)


@router.post("/analysis/{run_id}/stop", response_model=AnalysisRunRead)
async def stop_analysis(
    run_id: str, request: Request, session: SessionDependency
) -> AnalysisRunRead:
    run = AnalysisRunService(session).request_stop(run_id)
    current_execution_id = run.current_execution_id
    session.commit()
    if current_execution_id:
        await request.app.state.sandbox_executor.stop(current_execution_id)
    return AnalysisRunRead.model_validate(run)


@router.post("/analysis/{run_id}/retry", response_model=AnalysisRunRead, status_code=202)
async def retry_analysis(
    run_id: str, request: Request, session: SessionDependency
) -> AnalysisRunRead:
    run = AnalysisRunService(session).retry(run_id)
    runner = build_orchestrator(request)
    session.commit()
    schedule(request, run_id, runner.run(run_id))
    return AnalysisRunRead.model_validate(run)


@router.post("/analysis/{run_id}/report", response_model=AnalysisRunRead, status_code=202)
async def regenerate_report(
    run_id: str, request: Request, session: SessionDependency
) -> AnalysisRunRead:
    runner = build_orchestrator(request)
    run = AnalysisRunService(session).get(run_id)
    run.status = "running"
    run.state = "REPORT"
    run.error_message = None
    run.cancellation_requested = False
    run.updated_at = datetime.now(UTC)
    session.commit()
    schedule(request, run_id, runner.regenerate_report(run_id))
    return AnalysisRunRead.model_validate(run)


@router.get("/analysis/{run_id}/events")
async def analysis_events(
    run_id: str,
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    after = int(last_event_id or 0)

    async def stream() -> AsyncIterator[str]:
        cursor = after
        while True:
            if await request.is_disconnected():
                return
            with request.app.state.database.session() as session:
                run = AnalysisRunService(session).get(run_id)
                events = list(
                    session.scalars(
                        select(RuntimeEvent)
                        .where(
                            RuntimeEvent.run_id == run_id,
                            RuntimeEvent.sequence > cursor,
                        )
                        .order_by(RuntimeEvent.sequence)
                    )
                )
                status = run.status
            for event in events:
                cursor = event.sequence
                payload = {
                    "event": event.event_type,
                    "run_id": run_id,
                    "data": json.loads(event.data_json),
                }
                data = json.dumps(payload, ensure_ascii=False)
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"
            if status in TERMINAL_STATUSES and not events:
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
