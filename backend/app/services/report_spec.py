"""Ordered editorial report specification shared by planner and renderer."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from app.schemas.findings import NarrativeRole
from app.services.metric_contract import MetricDefinition
from app.services.report_evidence import (
    ArtifactUsage,
    EvidenceRole,
    KpiRole,
    ValueFormat,
    ValueScale,
    VisualPurpose,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


PathPart = StrictStr | StrictInt


class JsonValueSelector(StrictModel):
    path: list[PathPart] = Field(default_factory=list)


class TableCellSelector(StrictModel):
    records_path: list[PathPart] = Field(default_factory=list)
    row: int = Field(ge=0)
    field: str = Field(min_length=1)


class ScalarRef(StrictModel):
    source_id: str = Field(min_length=1)
    selector: JsonValueSelector | TableCellSelector


class SourceSpec(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    artifact_path: str = Field(min_length=1)
    kind: Literal["csv", "json", "image"]
    # The planner may leave this blank. The server fills and verifies it.
    sha256: str = Field(default="", pattern=r"^[0-9a-fA-F]{0,64}$")
    media_type: str = Field(min_length=1, max_length=120)
    usage: ArtifactUsage


class KpiSpec(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    label: str = Field(min_length=1, max_length=200)
    canonical_name: str | None = Field(default=None, min_length=1, max_length=200)
    display_label: str | None = Field(default=None, min_length=1, max_length=120)
    definition_note: str | None = Field(default=None, min_length=1, max_length=500)
    metric: str = Field(min_length=1, max_length=160)
    value_ref: ScalarRef | None = None
    format: ValueFormat
    decimals: int = Field(ge=0, le=6)
    unit: str | None = Field(default=None, max_length=40)
    scale: ValueScale = 1
    finding_ids: list[str] = Field(default_factory=list)
    purpose: str = Field(min_length=1, max_length=500)
    role: KpiRole | None = None
    presentation_roles: list[KpiRole] = Field(default_factory=list, max_length=2)
    evidence_role: EvidenceRole = "primary"
    supports_claim_ids: list[str] = Field(default_factory=list)
    metric_definition: MetricDefinition | None = None

    @model_validator(mode="after")
    def validate_role(self) -> "KpiSpec":
        roles = list(dict.fromkeys(self.presentation_roles))
        if self.role is not None and self.role not in roles:
            roles.append(self.role)
        if not roles:
            roles = ["overview"]
        if "evidence" in roles and (not self.finding_ids or not self.supports_claim_ids):
            raise ValueError("evidence KPI requires finding_ids and supports_claim_ids")
        self.presentation_roles = roles
        self.role = self.role or roles[0]
        self.display_label = self.display_label or self.label
        return self


class KpiGridBlock(StrictModel):
    type: Literal["kpi_grid"]
    kpi_ids: list[str] = Field(default_factory=list)
    presentation_role: KpiRole = "evidence"


class SeriesSpec(StrictModel):
    field: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=120)
    metric: str = Field(min_length=1, max_length=160)
    format: ValueFormat = "number"
    decimals: int = Field(default=0, ge=0, le=6)
    unit: str | None = Field(default=None, max_length=40)
    scale: ValueScale = 1
    visual_type: Literal["bar", "line", "area", "scatter"] | None = None
    axis: Literal["left", "right"] = "left"
    metric_definition: MetricDefinition | None = None
    presentation_usable: bool = True


class ChartSpec(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    chart_type: Literal[
        "line",
        "area",
        "bar",
        "horizontal_bar",
        "grouped_bar",
        "stacked_bar",
        "combo",
        "pie",
        "donut",
        "scatter",
    ]
    title: str = Field(min_length=1, max_length=240)
    subtitle: str | None = Field(default=None, max_length=500)
    finding_ids: list[str] = Field(default_factory=list)
    purpose: str = Field(min_length=1, max_length=500)
    evidence_role: EvidenceRole = "primary"
    source_id: str = Field(min_length=1)
    records_path: list[PathPart] = Field(default_factory=list)
    x_field: str = Field(min_length=1)
    x_display_label: str | None = Field(default=None, max_length=120)
    x_semantic: Literal["category", "date", "datetime", "month", "year", "temporal"] = "category"
    series: list[SeriesSpec] = Field(min_length=1, max_length=12)
    sort_by: str | None = None
    sort_order: Literal["source", "asc", "desc"] = "source"
    row_limit: int = Field(default=50, ge=1, le=500)
    source_caption: str = Field(min_length=1, max_length=300)
    show_legend: bool = True
    show_labels: bool = True
    supports_claim_ids: list[str] = Field(default_factory=list)
    visual_purpose: VisualPurpose = "analytical"
    visual_priority: float = Field(default=0.5, ge=0, le=1)
    visual_rationale: str | None = Field(default=None, min_length=1, max_length=500)


class ChartBlock(StrictModel):
    type: Literal["chart"]
    chart: ChartSpec


class TableColumnSpec(StrictModel):
    field: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=120)
    format: ValueFormat | Literal["text"] = "text"
    decimals: int = Field(default=0, ge=0, le=6)
    unit: str | None = Field(default=None, max_length=40)
    scale: ValueScale = 1
    metric: str | None = Field(default=None, max_length=160)
    metric_definition: MetricDefinition | None = None
    semantic_type: Literal[
        "text",
        "identifier",
        "integer",
        "decimal",
        "currency",
        "percentage_fraction",
        "percentage_points",
        "date",
        "datetime",
    ] = "text"
    presentation_usable: bool = True


class TableBlock(StrictModel):
    type: Literal["table"]
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=240)
    finding_ids: list[str] = Field(default_factory=list)
    purpose: str = Field(min_length=1, max_length=500)
    evidence_role: EvidenceRole = "supporting"
    usage: Literal["summary_table", "appendix"] = "summary_table"
    records_path: list[PathPart] = Field(default_factory=list)
    columns: list[TableColumnSpec] = Field(min_length=1, max_length=30)
    row_limit: int = Field(default=12, ge=1, le=200)
    supports_claim_ids: list[str] = Field(default_factory=list)
    visual_priority: float = Field(default=0.5, ge=0, le=1)
    visual_rationale: str | None = Field(default=None, min_length=1, max_length=500)


class ArtifactImageBlock(StrictModel):
    type: Literal["artifact_image"]
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    source_id: str = Field(min_length=1)
    finding_ids: list[str] = Field(default_factory=list)
    purpose: str = Field(min_length=1, max_length=500)
    evidence_role: EvidenceRole = "supporting"
    alt: str = Field(min_length=1, max_length=300)
    caption: str | None = Field(default=None, max_length=300)
    supports_claim_ids: list[str] = Field(default_factory=list)
    visual_purpose: VisualPurpose = "analytical"
    visual_priority: float = Field(default=0.5, ge=0, le=1)
    visual_rationale: str | None = Field(default=None, min_length=1, max_length=500)


class NarrativeBlock(StrictModel):
    type: Literal["narrative"]
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    finding_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    composite_insight_ids: list[str] = Field(default_factory=list)
    purpose: str = Field(min_length=1, max_length=500)
    display_role: Literal[
        "lead", "supporting_narrative", "evidence_interpretation", "limitation"
    ] = "supporting_narrative"
    related_block_id: str | None = Field(default=None, max_length=300)
    related_block_ids: list[str] = Field(default_factory=list, max_length=8)
    metric_refs: list[str] = Field(default_factory=list, max_length=12)


class CalloutBlock(StrictModel):
    type: Literal["callout"]
    tone: Literal["insight", "risk", "note"] = "insight"
    title: str | None = Field(default=None, max_length=160)
    text: str = Field(min_length=1, max_length=1200)


class RecommendationItemSpec(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    text: str = Field(min_length=1, max_length=500)
    priority: Literal["immediate", "near_term", "monitor"]
    source_finding_ids: list[str] = Field(default_factory=list)
    source_claim_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source(self) -> "RecommendationItemSpec":
        if not self.source_finding_ids and not self.source_claim_ids:
            raise ValueError("recommendation requires a finding, claim or risk source")
        return self


class RecommendationBlock(StrictModel):
    type: Literal["recommendations"]
    items: list[RecommendationItemSpec] = Field(min_length=1, max_length=30)


VisualItem = Annotated[ChartBlock | TableBlock, Field(discriminator="type")]


class VisualGroupBlock(StrictModel):
    type: Literal["visual_group"]
    layout: Literal["two-column", "stack"] = "two-column"
    items: list[VisualItem] = Field(min_length=1, max_length=4)


BlockSpec = Annotated[
    KpiGridBlock
    | ChartBlock
    | TableBlock
    | ArtifactImageBlock
    | NarrativeBlock
    | CalloutBlock
    | RecommendationBlock
    | VisualGroupBlock,
    Field(discriminator="type"),
]


class SectionSpec(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    title: str = Field(min_length=1, max_length=240)
    finding_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    composite_insight_ids: list[str] = Field(default_factory=list)
    narrative_role: NarrativeRole = "context"
    priority: int = Field(default=50, ge=1, le=100)
    visual_strategy: Literal[
        "balanced",
        "chart_led",
        "table_led",
        "narrative_led",
        "kpi_led",
        "risk_led",
        "context_only",
        "none",
    ] = "balanced"
    editorial_rationale: str | None = Field(default=None, min_length=1, max_length=500)
    layout: Literal["flow", "two-column", "visual-focus"] = "flow"
    blocks: list[BlockSpec] = Field(min_length=1, max_length=50)


class ProvenanceSpec(StrictModel):
    planner_mode: Literal["llm", "fallback"]
    findings_path: str = "analysis/findings.json"
    source_artifacts: list[str] = Field(default_factory=list)


class CompositeInsightSpec(StrictModel):
    id: str = Field(pattern=r"^insight_[A-Za-z0-9_-]+$")
    statement: str = Field(min_length=1, max_length=500)
    claim_ids: list[str] = Field(min_length=2, max_length=8)
    narrative_role: NarrativeRole


class StorylineSpec(StrictModel):
    headline_claim_id: str | None = Field(default=None, pattern=r"^claim_[A-Za-z0-9_-]+$")
    headline_claim_ids: list[str] = Field(default_factory=list, max_length=5)
    executive_summary_claim_ids: list[str] = Field(min_length=1, max_length=5)
    primary_claim_ids: list[str] = Field(default_factory=list, max_length=8)
    secondary_claim_ids: list[str] = Field(default_factory=list, max_length=12)
    supporting_claim_ids: list[str] = Field(default_factory=list, max_length=20)
    composite_insights: list[CompositeInsightSpec] = Field(default_factory=list, max_length=10)
    headline_composite_insight_id: str | None = Field(
        default=None, pattern=r"^insight_[A-Za-z0-9_-]+$"
    )

    @model_validator(mode="after")
    def normalize_headline(self) -> "StorylineSpec":
        headline_ids = list(dict.fromkeys(self.headline_claim_ids))
        if self.headline_claim_id is not None and self.headline_claim_id not in headline_ids:
            headline_ids.insert(0, self.headline_claim_id)
        if not headline_ids:
            raise ValueError("storyline requires at least one headline claim")
        self.headline_claim_ids = headline_ids
        self.headline_claim_id = headline_ids[0]
        if not self.primary_claim_ids:
            self.primary_claim_ids = list(headline_ids)
        return self


class ReportSpec(StrictModel):
    schema_version: Literal["3.0"]
    locale: Literal["zh-CN"]
    analysis_topic: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=300)
    subtitle: str | None = Field(default=None, max_length=500)
    theme: Literal["editorial"] = "editorial"
    summary_ref: Literal["findings.summary"] = "findings.summary"
    executive_summary: str | None = Field(default=None, min_length=1, max_length=2000)
    storyline: StorylineSpec | None = None
    sources: list[SourceSpec] = Field(default_factory=list)
    kpis: list[KpiSpec] = Field(default_factory=list)
    sections: list[SectionSpec] = Field(min_length=1, max_length=50)
    provenance: ProvenanceSpec


class ReportSpecDraft(ReportSpec):
    """Planner-facing name kept distinct for provider telemetry and JSON Schema."""

    pass


def report_spec_json_schema() -> dict:
    """Return the formal JSON Schema sent to structured LLM providers."""

    return ReportSpec.model_json_schema()
