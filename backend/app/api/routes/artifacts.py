from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.models import Artifact
from app.schemas.artifacts import ArtifactRead
from app.services.projects import ProjectService
from app.services.workspace import WorkspaceService

router = APIRouter(prefix="/projects/{project_id}/artifacts", tags=["artifacts"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("", response_model=list[ArtifactRead])
def list_artifacts(
    project_id: str,
    request: Request,
    session: SessionDependency,
    artifact_type: Annotated[str | None, Query(alias="type")] = None,
) -> list[ArtifactRead]:
    ProjectService(session, WorkspaceService(request.app.state.settings.workspace_root)).get(
        project_id
    )
    statement = select(Artifact).where(Artifact.project_id == project_id)
    if artifact_type:
        statement = statement.where(Artifact.artifact_type == artifact_type)
    return [
        ArtifactRead.model_validate(item)
        for item in session.scalars(statement.order_by(Artifact.created_at))
    ]
