import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NarrativeRole = Literal[
    "overall_outcome",
    "change",
    "trend",
    "driver",
    "breakdown",
    "composition",
    "efficiency",
    "concentration",
    "anomaly",
    "risk",
    "data_quality",
    "recommendation",
    "context",
]

ReportRole = Literal[
    "business_insight",
    "report_limitation",
    "internal_diagnostic",
]


class ClaimEvidenceGroup(BaseModel):
    """One jointly required evidence set for one atomic statement."""

    model_config = ConfigDict(extra="forbid")

    metric_ids: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    narrative_evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_non_empty(self) -> "ClaimEvidenceGroup":
        if not (self.metric_ids or self.artifact_paths or self.narrative_evidence):
            raise ValueError("claim evidence group must declare at least one evidence channel")
        return self


class Claim(BaseModel):
    """One independently verifiable statement inside a Finding."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    claim_id: str = Field(alias="id", pattern=r"^claim_[A-Za-z0-9_-]+$")
    statement: str = Field(min_length=1, max_length=500)
    priority: Literal["primary", "secondary", "supporting"] = "secondary"
    narrative_role: NarrativeRole = "context"
    report_role: ReportRole = "business_insight"
    strength: float = Field(default=0.5, ge=0, le=1)
    evidence_metric_ids: list[str] = Field(default_factory=list)
    evidence_artifact_paths: list[str] = Field(default_factory=list)
    narrative_evidence: list[str] = Field(default_factory=list)
    evidence_groups: list[ClaimEvidenceGroup] = Field(default_factory=list, max_length=1)

    @model_validator(mode="after")
    def normalize_evidence_group(self) -> "Claim":
        if not self.evidence_groups:
            return self
        group = self.evidence_groups[0]
        legacy = (
            self.evidence_metric_ids,
            self.evidence_artifact_paths,
            self.narrative_evidence,
        )
        grouped = (group.metric_ids, group.artifact_paths, group.narrative_evidence)
        if any(legacy) and legacy != grouped:
            raise ValueError("claim evidence group conflicts with flattened evidence fields")
        self.evidence_metric_ids = list(group.metric_ids)
        self.evidence_artifact_paths = list(group.artifact_paths)
        self.narrative_evidence = list(group.narrative_evidence)
        return self

    @model_validator(mode="after")
    def require_metric_for_quantitative_claim(self) -> "Claim":
        """Reject numeric business claims before they reach the orchestration loop."""

        if self.is_quantitative and not self.evidence_metric_ids:
            raise ValueError(
                "quantitative business claims must declare at least one evidence_metric_id"
            )
        return self

    @property
    def is_quantitative(self) -> bool:
        """Whether this business claim states an explicit numeric result."""

        if self.report_role != "business_insight":
            return False
        return bool(re.search(r"(?<![A-Za-z])\d+(?:[,.]\d+)?%?(?![A-Za-z])", self.statement))

    @property
    def id(self) -> str:
        """Compatibility alias matching Finding's ``id`` convention."""

        return self.claim_id


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^finding_[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=300)
    evidence: list[str] = Field(min_length=1)
    risk: str = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    related_artifacts: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_claim_ids(self) -> "Finding":
        ids = [claim.claim_id for claim in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("Finding contains duplicate claim IDs")
        return self


class Findings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    findings: list[Finding] = Field(min_length=1)
