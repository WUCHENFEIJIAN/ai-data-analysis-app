from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from app.schemas.findings import Claim, ClaimEvidenceGroup
from app.services.metric_contract import MetricDefinition
from app.services.report_evidence import ReportEvidenceManifest
from app.services.report_ready_artifacts import (
    AnalysisArtifactContract,
    ReportReadyArtifact,
    ScalarArtifactContract,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanTask(StrictModel):
    id: str = Field(pattern=r"^task_[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=160)
    goal: str = Field(min_length=1, max_length=1000)
    sequence: int = Field(ge=1)


class AskUserAction(StrictModel):
    action: Literal["ask_user"]
    question: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)


class CreatePlanAction(StrictModel):
    action: Literal["create_plan"]
    title: str = Field(min_length=1, max_length=200)
    analysis_topic: str | None = Field(default=None, min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=2000)
    tasks: list[PlanTask] = Field(min_length=1, max_length=20)


class ExecutePythonAction(StrictModel):
    action: Literal["execute_python"]
    task_id: str = Field(pattern=r"^task_[A-Za-z0-9_-]+$")
    filename: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=100_000)
    purpose: str = Field(min_length=1, max_length=1000)
    expected_artifacts: list[str] = Field(
        default_factory=list,
        description="Workspace-relative output paths this computation must create or change.",
    )
    artifact_contracts: list[AnalysisArtifactContract] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Creation-time semantic declarations for presentation-usable tabular outputs. "
            "Use only for dimensional CSV/tabular outputs: every metric nested in one "
            "AnalysisArtifactContract belongs to that artifact's grain, must be a "
            "reusable_measure, and its grain must exactly equal the artifact grain. This "
            "includes ratio, rate, and percentage measure fields; do not omit grain or move a "
            "grouped rate to scalar_evidence merely because each cell is scalar. Reusable "
            "measure value must be omitted or null. "
            "Dataset-level totals, record counts, quality counts, and other scalar observations "
            "belong in scalar_artifact_contracts as scalar_evidence JSON metrics. Each declaration "
            "is validated against the physical Artifact after execution and persisted immediately; "
            "omit intermediate or diagnostic outputs."
        ),
    )
    scalar_artifact_contracts: list[ScalarArtifactContract] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Creation-time declarations for scalar_evidence values in JSON Artifacts. "
            "Use this only for one materialized dataset-level observation, including an overall "
            "rate; do not put a multi-row grouped measure here merely because each cell is scalar, "
            "and do not put grouped ratio/rate/percentage fields here. Dataset-level totals, "
            "overall_record_count, quality counts, and other single observations belong here and "
            "must have a materialized value; do not put them in artifact_contracts. Declare every "
            "scalar "
            "value that Findings may cite later; the application verifies the source field and "
            "value before registering it in analysis/metrics.json."
        ),
    )

    @model_validator(mode="after")
    def validate_contract_ownership(self) -> "ExecutePythonAction":
        self.artifact_contracts = [
            contract.model_copy(update={"origin_task_id": self.task_id})
            for contract in self.artifact_contracts
        ]
        paths = [contract.artifact_path for contract in self.artifact_contracts]
        scalar_paths = [contract.artifact_path for contract in self.scalar_artifact_contracts]
        all_paths = [*paths, *scalar_paths]
        if len(all_paths) != len(set(all_paths)):
            raise ValueError("execute_python artifact contracts must have unique paths")
        for contract in [*self.artifact_contracts, *self.scalar_artifact_contracts]:
            if contract.artifact_path not in self.expected_artifacts:
                raise ValueError("artifact contract path must be listed in expected_artifacts")
        return self


class FindingDraft(StrictModel):
    id: str = Field(pattern=r"^finding_[A-Za-z0-9_-]+$")
    title: str
    evidence: list[str] = Field(min_length=1)
    risk: str
    recommendation: str
    related_artifacts: list[str] = Field(
        default_factory=list,
        description=(
            "Workspace-relative paths to files that already exist. Planned or expected "
            "outputs are not Artifacts until they have been generated."
        ),
    )
    claims: list[Claim] = Field(default_factory=list)


class ClaimRepairReplacement(StrictModel):
    """Evidence-only replacement for one directly affected Claim."""

    finding_id: str = Field(pattern=r"^finding_[A-Za-z0-9_-]+$")
    claim_id: str = Field(pattern=r"^claim_[A-Za-z0-9_-]+$")
    evidence_metric_ids: list[str] | None = None
    evidence_artifact_paths: list[str] | None = None
    evidence_groups: list[ClaimEvidenceGroup] | None = Field(
        default=None, min_length=1, max_length=1
    )

    @model_validator(mode="after")
    def validate_replacement_shape(self) -> "ClaimRepairReplacement":
        flat_provided = (
            self.evidence_metric_ids is not None or self.evidence_artifact_paths is not None
        )
        grouped_provided = self.evidence_groups is not None
        if not flat_provided and not grouped_provided:
            raise ValueError("claim repair replacement must change an evidence field")
        if self.evidence_metric_ids is not None and not self.evidence_metric_ids:
            raise ValueError("claim repair evidence_metric_ids must be non-empty")
        if flat_provided and grouped_provided:
            raise ValueError(
                "claim repair replacement must use flattened evidence fields or evidence_groups"
            )
        return self


class RecommendationRepairReplacement(StrictModel):
    """Replacement for one affected Finding recommendation."""

    finding_id: str = Field(pattern=r"^finding_[A-Za-z0-9_-]+$")
    recommendation: str = Field(min_length=1)


class CompleteAnalysisRepairResult(StrictModel):
    """Issue-scoped typed replacements merged into the selected full candidate."""

    repair_type: Literal["metric", "provenance", "recommendation"] | None = None
    metric_replacements: list[MetricDefinition] = Field(default_factory=list, max_length=20)
    claim_replacements: list[ClaimRepairReplacement] = Field(default_factory=list, max_length=40)
    recommendation_replacements: list[RecommendationRepairReplacement] = Field(
        default_factory=list, max_length=20
    )

    @model_validator(mode="after")
    def require_replacement(self) -> "CompleteAnalysisRepairResult":
        if not any(
            (
                self.metric_replacements,
                self.claim_replacements,
                self.recommendation_replacements,
            )
        ):
            raise ValueError("partial repair result must contain at least one typed replacement")
        if any(metric.metric_scope != "scalar_evidence" for metric in self.metric_replacements):
            raise ValueError("complete_analysis repair may replace only scalar_evidence metrics")
        return self


class CompleteAnalysisAction(StrictModel):
    action: Literal["complete_analysis"]
    summary: str = Field(min_length=1)
    findings: list[FindingDraft] = Field(min_length=1)
    scalar_metrics: list[MetricDefinition] = Field(
        default_factory=list,
        max_length=80,
        description=(
            "Claim-specific scalar_evidence Metric Definitions created during completion. "
            "Every evidence_metric_ids value not already present in the available metric "
            "directory must have its complete MetricDefinition here; an ID string alone is "
            "only a reference and is not a definition. Reusable measures are already persisted "
            "by execute_python artifact_contracts and must only be referenced here."
        ),
    )
    metrics: list[MetricDefinition] = Field(
        default_factory=list,
        max_length=80,
        description=(
            "Deprecated compatibility input for scalar_metrics. Existing reusable measures "
            "may be repeated read-only, but complete_analysis cannot create or modify them. "
            "Do not use metric IDs as a substitute for a complete scalar MetricDefinition."
        ),
    )
    referenced_metric_ids: list[str] = Field(default_factory=list, max_length=160)
    referenced_artifact_paths: list[str] = Field(default_factory=list, max_length=160)
    report_ready_artifacts: list[ReportReadyArtifact] = Field(
        default_factory=list,
        max_length=50,
        description=(
            "Deprecated read-only compatibility input. Report-ready Artifact declarations "
            "are owned and persisted by execute_python artifact_contracts."
        ),
    )

    @model_validator(mode="after")
    def validate_completion_owned_metrics(self) -> "CompleteAnalysisAction":
        for metric in self.scalar_metrics:
            if metric.metric_scope != "scalar_evidence":
                raise ValueError("scalar_metrics may contain only scalar_evidence definitions")
        metric_ids = [metric.metric_id for metric in [*self.scalar_metrics, *self.metrics]]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError(
                "complete_analysis metric definitions must have unique metric_id values"
            )
        if len(self.referenced_metric_ids) != len(set(self.referenced_metric_ids)):
            raise ValueError("referenced_metric_ids must be unique")
        if len(self.referenced_artifact_paths) != len(set(self.referenced_artifact_paths)):
            raise ValueError("referenced_artifact_paths must be unique")
        return self


class DeclareReportEvidenceAction(ReportEvidenceManifest):
    action: Literal["declare_report_evidence"]


class GenerateReportAction(StrictModel):
    action: Literal["generate_report"]
    title: str = Field(min_length=1, max_length=200)
    style: str | None = Field(default=None, max_length=100)


AgentAction = Annotated[
    AskUserAction
    | CreatePlanAction
    | ExecutePythonAction
    | CompleteAnalysisAction
    | DeclareReportEvidenceAction
    | GenerateReportAction,
    Field(discriminator="action"),
]

PlanningAction = Annotated[AskUserAction | CreatePlanAction, Field(discriminator="action")]


class AgentActionResponse(RootModel[AgentAction]):
    pass


class PlanningActionResponse(RootModel[PlanningAction]):
    pass
