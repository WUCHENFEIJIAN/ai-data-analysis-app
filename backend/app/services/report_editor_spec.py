"""Ordered editorial decisions produced by the Report Editor LLM."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ChartType = Literal[
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


class ReportEditorKpi(StrictModel):
    metric_ref: str = Field(min_length=1, max_length=160)
    display_label: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=500)


class ReportEditorNarrativeBlock(StrictModel):
    type: Literal["narrative"] = "narrative"
    text: str = Field(min_length=1, max_length=4000)
    claim_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Existing finding claim ids only. Never invent claim ids.",
    )
    composite_insight_ids: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Leave empty. Composite insights are not assembled in this version.",
    )
    purpose: str | None = Field(default=None, max_length=500)
    display_role: Literal[
        "lead", "supporting_narrative", "evidence_interpretation", "limitation"
    ] = "supporting_narrative"
    related_block_id: str | None = Field(
        default=None,
        max_length=300,
        description="data_ref of the chart or table this interpretation explains.",
    )
    related_block_ids: list[str] = Field(default_factory=list, max_length=8)
    metric_refs: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Existing metric ids this narrative actually uses.",
    )


class ReportEditorKpiGridBlock(StrictModel):
    type: Literal["kpi_grid"] = "kpi_grid"
    metric_refs: list[str] = Field(min_length=1, max_length=8)
    presentation_role: Literal["overview", "evidence"] = "overview"


class ReportEditorChartBlock(StrictModel):
    type: Literal["chart"] = "chart"
    data_ref: str = Field(min_length=1, max_length=300)
    chart_type: ChartType
    x_field: str = Field(min_length=1, max_length=120)
    series: list[str] = Field(min_length=1, max_length=12)
    title: str = Field(min_length=1, max_length=240)
    purpose: str = Field(min_length=1, max_length=500)


class ReportEditorTableBlock(StrictModel):
    type: Literal["table"] = "table"
    data_ref: str = Field(min_length=1, max_length=300)
    columns: list[str] = Field(min_length=1, max_length=30)
    title: str = Field(min_length=1, max_length=240)
    purpose: str = Field(min_length=1, max_length=500)


class ReportEditorCalloutBlock(StrictModel):
    """Callout may only contain type, tone, title, and text."""

    type: Literal["callout"] = "callout"
    tone: Literal["insight", "risk", "note"] = "insight"
    title: str | None = Field(default=None, max_length=160)
    text: str = Field(min_length=1, max_length=1200)


class ReportEditorRecommendationItem(StrictModel):
    text: str = Field(min_length=1, max_length=500)
    priority: Literal["immediate", "near_term", "monitor"] = "near_term"
    source_finding_ids: list[str] = Field(default_factory=list, max_length=20)
    source_claim_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Existing finding claim ids only.",
    )


class ReportEditorRecommendationsBlock(StrictModel):
    type: Literal["recommendations"] = "recommendations"
    items: list[ReportEditorRecommendationItem] = Field(min_length=1, max_length=30)


ReportEditorVisualItem = Annotated[
    ReportEditorChartBlock | ReportEditorTableBlock,
    Field(discriminator="type"),
]


class ReportEditorVisualGroupBlock(StrictModel):
    type: Literal["visual_group"] = "visual_group"
    layout: Literal["two-column"] = "two-column"
    items: list[ReportEditorVisualItem] = Field(min_length=1, max_length=4)


ReportEditorBlock = Annotated[
    ReportEditorNarrativeBlock
    | ReportEditorKpiGridBlock
    | ReportEditorChartBlock
    | ReportEditorTableBlock
    | ReportEditorCalloutBlock
    | ReportEditorRecommendationsBlock
    | ReportEditorVisualGroupBlock,
    Field(discriminator="type"),
]


class ReportEditorSection(StrictModel):
    title: str = Field(min_length=1, max_length=240)
    lead: str | None = Field(default=None, min_length=1, max_length=4000)
    finding_refs: list[str] = Field(default_factory=list, max_length=20)
    claim_ids: list[str] = Field(default_factory=list, max_length=20)
    section_role: (
        Literal["narrative_led", "chart_led", "table_led", "kpi_led", "risk_led", "context_only"]
        | None
    ) = None
    layout: Literal["flow", "two-column", "visual-focus"] = "flow"
    blocks: list[ReportEditorBlock] = Field(min_length=1, max_length=50)


class ReportEditorSpec(StrictModel):
    headline: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=2000)
    kpis: list[ReportEditorKpi] = Field(default_factory=list, max_length=8)
    sections: list[ReportEditorSection] = Field(min_length=1, max_length=20)


class ReportEditorRevision(StrictModel):
    """Partial rewrite of affected sections. Never a full report regeneration."""

    sections: list[ReportEditorSection] = Field(min_length=1, max_length=20)
