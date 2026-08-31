from typing import Any, Literal

from pydantic import BaseModel, Field


class ExecutionResult(BaseModel):
    execution_id: str
    status: Literal["success", "failed", "timeout", "stopped"]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int = Field(ge=0)
    script_path: str
    new_artifacts: list[str] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    docker_executed: bool = True
    failure: dict[str, Any] | None = None
    script_fingerprint: str | None = None
    artifact_fingerprint_before: str | None = None
    artifact_fingerprint_after: str | None = None
    artifact_contract_issues: list[dict[str, Any]] = Field(default_factory=list)
    registered_report_schemas: list[str] = Field(default_factory=list)
    registered_reusable_metrics: list[str] = Field(default_factory=list)
    registered_scalar_metrics: list[str] = Field(default_factory=list)
