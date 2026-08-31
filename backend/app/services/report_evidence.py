"""Analysis-owned declarations for report-ready metrics and artifacts."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    model_validator,
)

from app.services.metric_contract import MetricDefinition

REPORT_EVIDENCE_GUIDANCE = """Report delivery contract:
- The canonical MVP path registers reusable Metric Definitions and report-ready field bindings in
  execute_python.artifact_contracts during the Analysis Action that creates each Artifact.
  complete_analysis references those persisted facts and may add only Claim-specific
  scalar_evidence metrics. report_evidence.json is legacy compatibility only.
- A report-ready Artifact declaration is a physical schema binding, not a second Metric Registry
  and not a visual plan. Declare only presentation-usable analytical CSV/JSON under data/.
- Each creation-time report-ready field declares role=dimension, measure or context. Every
  measure with presentation_usable=true must declare metric_ref, and that metric_ref resolves to
  reusable_measure Metric Definition in the same local Artifact Contract. Its
  source_artifact, source_field, and grain semantics must match the physical artifact field.
  Never invent an unregistered metric_ref.
- Register reusable measures, not one Metric Definition per dimension value. For rows such as
  category_a/metric_x, bind metric_x to the general metric_x definition; do not create
  metric_x_category_a, metric_x_category_b, and one metric per category merely to make a chart.
- MetricDefinition represents a metric concept. For a multi-row Artifact such as
  period/metric_x, declare metric_x with metric_scope=reusable_measure and omit value (or set it
  to null): the source field is the series. A specific observation such as metric_x_period_a may
  use metric_scope=scalar_evidence with a materialized value for Claim support, but it must not
  define the whole period/metric_x series. Never infer scope from metric or field names.
- Intermediate Analysis Artifacts do not need presentation bindings and must not declare an
  artifact_contract. They remain valid Analysis context but are not report-ready.
- Python must compute KPI values and save report-ready CSV/JSON under data/; never ask the
  renderer to aggregate. analysis/ is reserved for application-owned findings.json and the
  analysis-owned Metric Registry / legacy report_evidence.json manifest.
- Python must never create or modify analysis/report_evidence.json. Only when explicitly repairing
  an existing legacy manifest may declare_report_evidence be used.
- Never create or modify analysis/findings.json or analysis/metrics.json from Python. Return
  complete_analysis so the application validates and atomically replaces Findings and any
  scalar_evidence metrics. Reusable metrics and report-ready declarations are read-only there.
- Save chart-ready CSV/JSON under data/ for standard trends, rankings, comparisons and composition
  charts, then bind dimensions and measures in the same execute_python artifact_contracts Action.
- Save PNG/SVG only for special visuals that structured charts cannot express.
- Legacy repair only: declare_report_evidence uses the string schema_version "1.0". Its kpis
  declare id,
  label, artifact_path,
  selector, metric, format, finding_ids, purpose and evidence_role. Its artifacts declare
  artifact_path, usage, finding_ids, purpose and either chart/table metadata or image alt text.
- Preserve complete evidence coverage: every material Finding must retain sufficient KPI, chart or
  table support. Never remove a valid KPI or visual merely to pass validation. Every declared chart
  or table must include all required metadata; do not emit partial chart or table blocks.
- Publish a top-level metrics registry with one Metric Definition per core metric. Ratio metrics
  must name numerator and denominator metric IDs, state the formula in definition and declare a
  generic ratio_basis compatible with the denominator's count or quantity semantics.
- Every Metric Definition uses metric_id as its canonical identifier field. Never use id for a
  Metric Definition; id is used by other objects such as KPIs and Findings.
- Choose ratio_basis deterministically from the denominator: per_entity requires an entity or
  distinct count; per_event requires an event or row count; per_row requires a row count; and
  per_quantity requires a quantity metric. Use other only for a legitimate measure-to-measure
  ratio such as part / whole or delta / prior value, never to bypass a compatible count or
  quantity denominator.
- ratio_basis describes denominator semantics only. For percentage metrics, ratio_value_basis
  describes the canonical numeric representation: fraction means 0.081 and percent means 8.1.
  Numerator / denominator percentage ratios default to fraction; declare percent only when the
  canonical value is already stored in percent points.
- The ratio value must equal numerator / denominator in its declared numeric representation. For a
  derived formula such as
  (current - prior) / prior, declare a separate delta numerator metric and reference that metric;
  do not reference the current-period total as the numerator.
- Non-ratio metrics must omit ratio_basis, numerator and denominator. Never use ratio_basis="other"
  as a placeholder on sum, count, distinct_count, mean, min or max metrics.
- KPI/chart/table metric strings are references, not definitions. Every metric ID referenced by a
  Finding claim, KPI, chart series or table column must resolve through the top-level metrics
  registry or a matching inline metric_definition.
- Every Metric Definition declares semantic_type, unit_family and scale. Count metrics also
  declare count_semantics and is_distinct so field sums cannot be mistaken for unique entities.
- Scalar quantitative evidence that directly supports a report-ready Artifact declares
  source_field and an exact source_selector when the Artifact has multiple rows. The declared
  value, source_artifact and grain must reproduce the real observation. Never copy a value from
  one source or grain while naming a different Artifact as provenance.
- For a JSON object result, source_field may be a top-level key or a dot-delimited path to a
  numeric scalar. Do not invent a tabular selector for an object result. Analysis must materialize
  derived scalar evidence such as a period change in a structured result before complete_analysis,
  then reference that stored scalar. Do not point a delta at the current-period total and do not
  invent comparison selectors or formula expressions for the provenance validator.
- Every report-ready analytical Artifact has one observation grain. Do not concatenate overall
  rows with dimension-level rows. Split them into separate Artifacts, calculate every exposed
  measure at the same dimension grain, or expose only the measure supported at that grain.
- A declared analytical dimension must identify observations rather than repeat an almost-constant
  scope label. Presentation measures intended for one chart need compatible non-null row coverage;
  missing values remain missing and must never be filled or rendered as numeric zero.
- Findings may contain atomic claims with claim_id, statement and evidence_metric_ids.
  Quantitative business-insight Claims must bind at least one evidence_metric_id; pure qualitative,
  limitation and scope Claims may use artifact or narrative evidence without a metric. KPI/chart/
  table/image declarations use supports_claim_ids to say exactly which claims they prove.
- Every KPI declares presentation_roles. A metric may be both overview and evidence. Evidence
  KPIs support at least one atomic claim; overview placement never removes that claim binding.
- Keep canonical_name/display_label separate. Display labels stay short while definition_note
  preserves the strict calculation wording.
- Valid usage values are none, evidence_only, visual_source, summary_table and appendix.
- Valid format values are number, integer, currency and percent. Valid evidence_role values are
  primary, supporting and context.
- KPI metric is required. Chart sort_order values are source, asc and desc. Table metadata does not
  accept source_caption.
- Every selector must include type=json with path, or type=table with records_path, row and field.
- Every finding_ids and supports_claim_ids list must exactly reuse IDs from the current
  analysis/findings.json; never invent aliases such as finding_overview for finding_overall.
- evidence_only and none artifacts must not include chart, table or alt metadata.
- Unselected raw and intermediate artifacts stay usage=none and must not become report content.
- Return one complete but compact JSON object. Omit optional fields when they are not used; never
  emit null placeholders for optional Metric, KPI, chart, table or Artifact fields.

Canonical completion example after execute_python has persisted the reusable contract:
{
  "action": "complete_analysis",
  "summary": "Verified comparison",
  "findings": [{
    "id": "finding_1",
    "title": "Metric X differs by category",
    "evidence": ["data/category_comparison.csv contains the verified comparison"],
    "risk": "The measured categories differ",
    "recommendation": "Monitor the category comparison",
    "related_artifacts": ["data/category_comparison.csv"],
    "claims": [{
      "id": "claim_1",
      "statement": "Category A records Metric X at 42%",
      "evidence_metric_ids": ["metric_x"],
      "evidence_artifact_paths": ["data/category_comparison.csv"]
    }]
  }],
  "scalar_metrics": [],
  "referenced_metric_ids": ["metric_x"],
  "referenced_artifact_paths": ["data/category_comparison.csv"]
}

Legacy manifest example (use only to repair an existing legacy declaration):
{
  "action": "declare_report_evidence",
  "schema_version": "1.0",
  "metrics": [{
    "metric_id": "total_value",
    "metric_scope": "scalar_evidence",
    "label": "Total value",
    "value": 100,
    "aggregation": "sum",
    "semantic_type": "measure",
    "unit_family": "currency",
    "scale": 1,
    "unit": "USD",
    "definition": "Sum of the declared measure over the analysis scope",
    "source_artifact": "data/summary.json"
  }, {
    "metric_id": "period_value",
    "metric_scope": "reusable_measure",
    "label": "Period value",
    "value": null,
    "aggregation": "sum",
    "semantic_type": "measure",
    "unit_family": "currency",
    "scale": 1,
    "unit": "USD",
    "grain": "period",
    "definition": "Sum of the declared measure for each period",
    "source_artifact": "data/trend.csv",
    "source_field": "metric_x"
  }],
  "kpis": [{
    "id": "total_value",
    "label": "Total value",
    "display_label": "Total value",
    "definition_note": "Sum of the declared measure over the analysis scope",
    "metric": "total_value",
    "artifact_path": "data/summary.json",
    "selector": {"type": "json", "path": ["total_value"]},
    "format": "number",
    "decimals": 0,
    "scale": 1,
    "finding_ids": ["finding_1"],
    "purpose": "Quantify the primary finding",
    "presentation_roles": ["overview", "evidence"],
    "evidence_role": "primary",
    "supports_claim_ids": ["claim_1"]
  }],
  "artifacts": [{
    "artifact_path": "data/trend.csv",
    "usage": "visual_source",
    "finding_ids": ["finding_1"],
    "purpose": "Show the measured trend",
    "evidence_role": "supporting",
    "supports_claim_ids": ["claim_1"],
    "chart": {
      "chart_type": "line",
      "title": "Measured trend",
      "x_field": "period",
      "series": [{
        "field": "value",
        "label": "Value",
        "metric": "period_value",
        "format": "number",
        "decimals": 0,
        "scale": 1,
        "axis": "left"
      }],
      "records_path": [],
      "sort_order": "source",
      "row_limit": 50,
      "source_caption": "Source: data/trend.csv",
      "show_legend": true,
      "show_labels": true,
      "supports_claim_ids": ["claim_1"],
      "visual_purpose": "analytical"
    }
  }]
}
"""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


PathPart = StrictStr | StrictInt
ArtifactUsage = Literal["none", "evidence_only", "visual_source", "summary_table", "appendix"]
EvidenceRole = Literal["primary", "supporting", "context"]
KpiRole = Literal["overview", "evidence"]
VisualPurpose = Literal["analytical", "context"]
ValueFormat = Literal["number", "integer", "currency", "percent"]
ValueScale = float


class EvidenceJsonSelector(StrictModel):
    type: Literal["json"] = "json"
    path: list[PathPart] = Field(default_factory=list)


class EvidenceTableSelector(StrictModel):
    type: Literal["table"] = "table"
    records_path: list[PathPart] = Field(default_factory=list)
    row: int = Field(ge=0)
    field: str = Field(min_length=1)


EvidenceSelector = Annotated[
    EvidenceJsonSelector | EvidenceTableSelector, Field(discriminator="type")
]


class EvidenceKpi(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    label: str = Field(min_length=1, max_length=200)
    canonical_name: str | None = Field(default=None, min_length=1, max_length=200)
    display_label: str | None = Field(default=None, min_length=1, max_length=120)
    definition_note: str | None = Field(default=None, min_length=1, max_length=500)
    metric: str = Field(min_length=1, max_length=160)
    artifact_path: str = Field(min_length=1)
    selector: EvidenceSelector
    format: ValueFormat
    decimals: int = Field(default=0, ge=0, le=6)
    unit: str | None = Field(default=None, max_length=40)
    scale: ValueScale = 1
    finding_ids: list[str] = Field(default_factory=list)
    purpose: str = Field(min_length=1, max_length=500)
    role: KpiRole | None = None
    presentation_roles: list[KpiRole] = Field(default_factory=list, max_length=2)
    evidence_role: EvidenceRole = "primary"
    supports_claim_ids: list[str] = Field(default_factory=list)
    # Analysis may provide the complete semantic contract for this scalar.
    # It is optional during the compatibility window; new report readiness
    # manifests should populate it for every core KPI.
    metric_definition: MetricDefinition | None = None

    @model_validator(mode="after")
    def validate_role(self) -> "EvidenceKpi":
        roles = list(dict.fromkeys(self.presentation_roles))
        if self.role is not None and self.role not in roles:
            roles.append(self.role)
        if not roles:
            raise ValueError("KPI requires at least one presentation role")
        if "evidence" in roles and (not self.finding_ids or not self.supports_claim_ids):
            raise ValueError("evidence KPI requires finding_ids and supports_claim_ids")
        self.presentation_roles = roles
        self.role = self.role or roles[0]
        self.display_label = self.display_label or self.label
        return self


class EvidenceSeries(StrictModel):
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


class EvidenceChart(StrictModel):
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
    x_field: str = Field(min_length=1)
    series: list[EvidenceSeries] = Field(min_length=1, max_length=12)
    records_path: list[PathPart] = Field(default_factory=list)
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


class EvidenceTableColumn(StrictModel):
    field: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=120)
    format: ValueFormat | Literal["text"] = "text"
    decimals: int = Field(default=0, ge=0, le=6)
    unit: str | None = Field(default=None, max_length=40)
    scale: ValueScale = 1
    metric: str | None = Field(default=None, max_length=160)
    metric_definition: MetricDefinition | None = None


class EvidenceTable(StrictModel):
    title: str = Field(min_length=1, max_length=240)
    columns: list[EvidenceTableColumn] = Field(min_length=1, max_length=30)
    records_path: list[PathPart] = Field(default_factory=list)
    row_limit: int = Field(default=20, ge=1, le=200)
    supports_claim_ids: list[str] = Field(default_factory=list)
    visual_priority: float = Field(default=0.5, ge=0, le=1)
    visual_rationale: str | None = Field(default=None, min_length=1, max_length=500)


class ArtifactEvidence(StrictModel):
    artifact_path: str = Field(min_length=1)
    usage: ArtifactUsage
    finding_ids: list[str] = Field(min_length=1)
    purpose: str = Field(min_length=1, max_length=500)
    evidence_role: EvidenceRole = "supporting"
    supports_claim_ids: list[str] = Field(default_factory=list)
    chart: EvidenceChart | None = None
    table: EvidenceTable | None = None
    alt: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_usage_payload(self) -> "ArtifactEvidence":
        if self.usage == "visual_source" and self.chart is None and not self.alt:
            raise ValueError("visual_source requires chart metadata or image alt text")
        if self.usage in {"summary_table", "appendix"} and self.table is None:
            raise ValueError("table usage requires table metadata")
        if self.usage in {"none", "evidence_only"} and (self.chart or self.table or self.alt):
            raise ValueError("non-visual artifact usage cannot declare render metadata")
        return self


class ReportEvidenceManifest(StrictModel):
    schema_version: Literal["1.0"]
    metrics: list[MetricDefinition] = Field(default_factory=list)
    kpis: list[EvidenceKpi] = Field(default_factory=list)
    artifacts: list[ArtifactEvidence] = Field(default_factory=list)


def collect_manifest_metrics(manifest: ReportEvidenceManifest) -> list[MetricDefinition]:
    """Return registry metrics plus any inline definitions that introduce new IDs."""

    metrics = list(manifest.metrics)
    known = {metric.metric_id for metric in metrics}

    def add(definition: MetricDefinition | None) -> None:
        if definition is None or definition.metric_id in known:
            return
        metrics.append(definition)
        known.add(definition.metric_id)

    for kpi in manifest.kpis:
        add(kpi.metric_definition)
    for artifact in manifest.artifacts:
        if artifact.chart:
            for series in artifact.chart.series:
                add(series.metric_definition)
        if artifact.table:
            for column in artifact.table.columns:
                add(column.metric_definition)
    return metrics


def manifest_metric_reference_issues(manifest: ReportEvidenceManifest) -> list[str]:
    """Validate every KPI, chart series and table column against Metric Definitions."""

    from app.services.metric_contract import build_metric_registry, metric_reference_issues

    registry, issues = build_metric_registry(collect_manifest_metrics(manifest))
    for kpi in manifest.kpis:
        issues.extend(
            metric_reference_issues(
                owner=f"KPI {kpi.id}",
                metric_id=kpi.metric,
                unit=kpi.unit or "",
                scale=kpi.scale,
                inline_definition=kpi.metric_definition,
                registry=registry,
            )
        )
    for artifact in manifest.artifacts:
        if artifact.chart:
            for series in artifact.chart.series:
                issues.extend(
                    metric_reference_issues(
                        owner=(
                            f"Chart {artifact.artifact_path} series {series.field}"
                        ),
                        metric_id=series.metric,
                        unit=series.unit or "",
                        scale=series.scale,
                        inline_definition=series.metric_definition,
                        registry=registry,
                    )
                )
        if artifact.table:
            for column in artifact.table.columns:
                if not column.metric:
                    continue
                issues.extend(
                    metric_reference_issues(
                        owner=(
                            f"Table {artifact.artifact_path} column {column.field}"
                        ),
                        metric_id=column.metric,
                        unit=column.unit or "",
                        scale=column.scale,
                        inline_definition=column.metric_definition,
                        registry=registry,
                    )
                )
    return list(dict.fromkeys(issues))
