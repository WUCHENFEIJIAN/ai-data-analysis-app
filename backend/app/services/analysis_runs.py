import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.models import AnalysisRun, Conversation, Message, RuntimeEvent

TERMINAL_STATUSES = {"completed", "failed", "stopped"}
ACTIVE_STATUSES = {"pending", "running", "waiting_user"}


class AnalysisRunService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, project_id: str, message: str) -> tuple[AnalysisRun, bool]:
        existing = self.session.scalar(
            select(AnalysisRun)
            .where(
                AnalysisRun.project_id == project_id,
                AnalysisRun.status.in_(ACTIVE_STATUSES),
            )
            .order_by(AnalysisRun.created_at.desc())
        )
        if existing is not None:
            return existing, False
        conversation = self.session.scalar(
            select(Conversation)
            .where(Conversation.project_id == project_id)
            .order_by(Conversation.created_at)
        )
        if conversation is None:
            raise NotFoundError("Conversation")
        run = AnalysisRun(
            id=f"run_{uuid.uuid4().hex}",
            project_id=project_id,
            conversation_id=conversation.id,
            user_request=message.strip(),
            status="pending",
            state="UNDERSTAND",
        )
        self.session.add(run)
        self.session.add(
            Message(
                id=f"msg_{uuid.uuid4().hex}",
                conversation_id=conversation.id,
                role="user",
                content=message.strip(),
                message_type="text",
            )
        )
        self.session.flush()
        self.event(run.id, "analysis.started", {"status": "pending"})
        return run, True

    def get(self, run_id: str) -> AnalysisRun:
        run = self.session.get(AnalysisRun, run_id)
        if run is None:
            raise NotFoundError("Analysis run")
        return run

    def latest_for_project(self, project_id: str) -> AnalysisRun | None:
        return self.session.scalar(
            select(AnalysisRun)
            .where(AnalysisRun.project_id == project_id)
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        )

    def event(self, run_id: str, event_type: str, data: dict[str, Any]) -> RuntimeEvent:
        sequence = (
            int(
                self.session.scalar(
                    select(func.coalesce(func.max(RuntimeEvent.sequence), 0)).where(
                        RuntimeEvent.run_id == run_id
                    )
                )
                or 0
            )
            + 1
        )
        event = RuntimeEvent(
            id=f"evt_{uuid.uuid4().hex}",
            run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            data_json=json.dumps(data, ensure_ascii=False),
        )
        self.session.add(event)
        self.session.flush()
        return event

    def resume(self, run_id: str, message: str) -> AnalysisRun:
        run = self.get(run_id)
        if run.status != "waiting_user":
            raise AppError("invalid_run_state", "Analysis run is not waiting for user input", 409)
        self.session.add(
            Message(
                id=f"msg_{uuid.uuid4().hex}",
                conversation_id=run.conversation_id,
                role="user",
                content=message.strip(),
                message_type="text",
            )
        )
        run.user_request = f"{run.user_request}\nUser clarification: {message.strip()}"
        run.status = "running"
        run.state = "CLARIFY"
        run.cancellation_requested = False
        run.updated_at = datetime.now(UTC)
        self.event(
            run.id,
            "analysis.status",
            {"status": "running", "message": "Analysis resumed"},
        )
        return run

    def retry(self, run_id: str) -> AnalysisRun:
        run = self.get(run_id)
        if run.status != "failed":
            raise AppError("invalid_run_state", "Only a failed analysis run can be retried", 409)
        previous_error = run.error_message
        run.status = "pending"
        run.error_message = None
        run.cancellation_requested = False
        run.current_execution_id = None
        run.step_count = 0
        run.execution_count = 0
        run.code_retry_count = 0
        run.complete_analysis_repair_state_json = None
        run.updated_at = datetime.now(UTC)
        self.event(
            run.id,
            "analysis.retry_started",
            {
                "status": "pending",
                "state": run.state,
                "previous_error": previous_error,
            },
        )
        return run

    def request_stop(self, run_id: str) -> AnalysisRun:
        run = self.get(run_id)
        if run.status in TERMINAL_STATUSES:
            return run
        run.cancellation_requested = True
        run.status = "stopped"
        run.complete_analysis_repair_state_json = None
        run.updated_at = datetime.now(UTC)
        self.event(run.id, "analysis.stopped", {"status": "stopped"})
        return run


def recover_interrupted_runs(session: Session) -> int:
    result = session.execute(
        update(AnalysisRun)
        .where(AnalysisRun.status.in_({"pending", "running"}))
        .values(
            status="failed",
            error_message="Analysis was interrupted by a service restart",
            complete_analysis_repair_state_json=None,
            updated_at=datetime.now(UTC),
        )
    )
    return result.rowcount
