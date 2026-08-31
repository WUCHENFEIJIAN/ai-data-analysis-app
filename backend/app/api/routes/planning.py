from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.core.errors import LLMError
from app.models import Conversation, Message
from app.schemas.actions import AskUserAction, CreatePlanAction
from app.schemas.messages import AnalysisPlanRequest, MessageRead
from app.services.planning import AnalysisPlanningService
from app.services.projects import ProjectService
from app.services.workspace import PathResolver, WorkspaceService
from app.skills.loader import SkillLoader

router = APIRouter(prefix="/projects/{project_id}", tags=["analysis"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post("/analysis/plan", response_model=AskUserAction | CreatePlanAction)
async def create_analysis_plan(
    project_id: str,
    payload: AnalysisPlanRequest,
    request: Request,
    session: SessionDependency,
) -> AskUserAction | CreatePlanAction:
    settings = request.app.state.settings
    ProjectService(session, WorkspaceService(settings.workspace_root)).get(project_id)
    provider = request.app.state.llm_provider
    if provider is None:
        raise LLMError("LLM provider is not configured", "llm_not_configured", 503)
    planner = AnalysisPlanningService(
        session,
        PathResolver(settings.workspace_root),
        SkillLoader(settings.skill_root),
        provider,
    )
    return await planner.create_plan(project_id, payload.message)


@router.get("/messages", response_model=list[MessageRead])
def list_messages(
    project_id: str, request: Request, session: SessionDependency
) -> list[MessageRead]:
    settings = request.app.state.settings
    ProjectService(session, WorkspaceService(settings.workspace_root)).get(project_id)
    statement = (
        select(Message)
        .join(Conversation)
        .where(Conversation.project_id == project_id)
        .order_by(Message.created_at)
    )
    return [MessageRead.model_validate(message) for message in session.scalars(statement)]
