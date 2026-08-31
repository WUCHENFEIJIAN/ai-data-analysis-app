from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.schemas.projects import ProjectCreate, ProjectList, ProjectRead
from app.services.projects import ProjectService
from app.services.workspace import WorkspaceService

router = APIRouter(prefix="/projects", tags=["projects"])
SessionDependency = Annotated[Session, Depends(get_session)]


def service(request: Request, session: Session) -> ProjectService:
    return ProjectService(session, WorkspaceService(request.app.state.settings.workspace_root))


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate, request: Request, session: SessionDependency
) -> ProjectRead:
    return ProjectRead.model_validate(service(request, session).create(payload.name))


@router.get("", response_model=ProjectList)
def list_projects(request: Request, session: SessionDependency) -> ProjectList:
    items = service(request, session).list()
    return ProjectList(items=[ProjectRead.model_validate(item) for item in items], total=len(items))


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, request: Request, session: SessionDependency) -> ProjectRead:
    return ProjectRead.model_validate(service(request, session).get(project_id))


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, request: Request, session: SessionDependency) -> Response:
    service(request, session).delete(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
