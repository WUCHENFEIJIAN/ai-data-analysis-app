import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.models import Conversation, Project
from app.services.workspace import WorkspaceService


class ProjectService:
    def __init__(self, session: Session, workspace_service: WorkspaceService) -> None:
        self.session = session
        self.workspace_service = workspace_service

    def create(self, name: str) -> Project:
        normalized_name = " ".join(name.split())
        if not normalized_name:
            raise ValidationError("Project name cannot be empty")
        project = Project(id=f"pj_{uuid.uuid4().hex}", name=normalized_name)
        conversation = Conversation(id=f"conv_{uuid.uuid4().hex}", project=project)
        self.session.add_all([project, conversation])
        try:
            self.workspace_service.create(project.id)
            self.session.flush()
        except Exception:
            self.session.rollback()
            self.workspace_service.delete(project.id)
            raise
        return project

    def list(self) -> list[Project]:
        return list(self.session.scalars(select(Project).order_by(Project.updated_at.desc())))

    def get(self, project_id: str) -> Project:
        project = self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project")
        return project

    def delete(self, project_id: str) -> None:
        project = self.get(project_id)
        self.workspace_service.delete(project_id)
        self.session.delete(project)
        self.session.flush()
