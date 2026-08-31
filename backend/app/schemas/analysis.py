from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalysisStartRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)


class AnalysisResumeRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)


class AnalysisRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    user_request: str
    analysis_topic: str | None
    status: Literal["pending", "running", "waiting_user", "completed", "failed", "stopped"]
    state: str
    step_count: int
    execution_count: int
    code_retry_count: int
    cancellation_requested: bool
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class RuntimeEventRead(BaseModel):
    id: str
    sequence: int
    event: str
    run_id: str
    data: dict[str, Any]
    created_at: datetime
