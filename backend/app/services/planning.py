import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.context import ContextBuilder
from app.core.errors import ValidationError
from app.llm.base import LLMProvider
from app.models import Conversation, Message
from app.schemas.actions import AskUserAction, CreatePlanAction, PlanningActionResponse
from app.services.workspace import PathResolver
from app.skills.loader import SkillLoader, SkillStage


class AnalysisPlanningService:
    def __init__(
        self,
        session: Session,
        resolver: PathResolver,
        skill_loader: SkillLoader,
        provider: LLMProvider,
    ) -> None:
        self.session = session
        self.resolver = resolver
        self.skill_loader = skill_loader
        self.provider = provider

    async def create_plan(
        self, project_id: str, user_request: str
    ) -> AskUserAction | CreatePlanAction:
        request = user_request.strip()
        if not request:
            raise ValidationError("Analysis request cannot be empty")
        profile_path = self.resolver.resolve(project_id, "context/dataset_profile.json")
        if not profile_path.is_file():
            raise ValidationError("Upload a readable dataset before starting analysis")
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        if not profile.get("files"):
            raise ValidationError("No readable dataset is available for analysis")
        conversation = self.session.scalar(
            select(Conversation)
            .where(Conversation.project_id == project_id)
            .order_by(Conversation.created_at)
        )
        if conversation is None:
            raise ValidationError("Project conversation is unavailable")
        self._message(conversation.id, "user", request, "text")
        skill = self.skill_loader.load(SkillStage.UNDERSTAND)
        messages = ContextBuilder().build_planning_context(request, profile, skill)
        response = await self.provider.structured_chat(messages, PlanningActionResponse)
        action = response.root
        if isinstance(action, CreatePlanAction):
            action = action.model_copy(
                update={"analysis_topic": action.analysis_topic or action.title}
            )
            plan_path = self.resolver.resolve(project_id, "plans/analysis_plan.json")
            plan_path.write_text(action.model_dump_json(indent=2), encoding="utf-8")
            self._message(conversation.id, "assistant", action.model_dump_json(), "plan")
        else:
            self._message(conversation.id, "assistant", action.question, "question")
        self.session.flush()
        return action

    def _message(self, conversation_id: str, role: str, content: str, message_type: str) -> None:
        self.session.add(
            Message(
                id=f"msg_{uuid.uuid4().hex}",
                conversation_id=conversation_id,
                role=role,
                content=content,
                message_type=message_type,
            )
        )
