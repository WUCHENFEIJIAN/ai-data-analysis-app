"""Conservative ReportSpec builder. Never guess KPI values or chart fields."""

from __future__ import annotations

from app.services.report_editor_assembler import ReportEditorAssembler
from app.services.report_editor_spec import (
    ReportEditorChartBlock,
    ReportEditorKpi,
    ReportEditorKpiGridBlock,
    ReportEditorNarrativeBlock,
    ReportEditorSection,
    ReportEditorSpec,
    ReportEditorTableBlock,
)
from app.services.report_inputs import ReportInputs
from app.services.report_reportability import business_findings_for_report
from app.services.report_spec import ReportSpec
from app.services.report_validator import ReportSpecValidator
from app.services.workspace import PathResolver


class FallbackSpecBuilder:
    """Build a short report from findings, metrics and explicit visual metadata."""

    def __init__(self, resolver: PathResolver | None = None) -> None:
        self.resolver = resolver

    def build(self, project_id: str, inputs: ReportInputs, style: str | None = None) -> ReportSpec:
        del project_id, style
        draft = self.build_editor_spec(inputs)
        result = ReportSpecValidator.validate(draft, inputs)
        spec = ReportEditorAssembler().assemble(result.spec, inputs, planner_mode="fallback")
        ReportSpecValidator.validate_assembled(spec, inputs)
        return spec

    def build_editor_spec(self, inputs: ReportInputs) -> ReportEditorSpec:
        reportable = business_findings_for_report(inputs.findings)
        headline = (reportable[0].title if reportable else inputs.findings.findings[0].title)
        primary = next(
            (
                claim.statement
                for finding in reportable
                for claim in finding.claims
                if claim.priority == "primary"
            ),
            None,
        )
        if primary:
            headline = primary
        kpis = [
            ReportEditorKpi(
                metric_ref=metric.metric_id,
                display_label=metric.label,
                purpose=metric.definition,
            )
            for metric in inputs.metrics[:4]
        ]
        declared_visuals = _declared_visuals(inputs)
        sections = []
        for finding in reportable[:6]:
            visual = next(
                item
                for item in declared_visuals
                if finding.id in item["finding_ids"] and item["kind"] == "chart"
            ) if any(
                finding.id in item["finding_ids"] and item["kind"] == "chart"
                for item in declared_visuals
            ) else None
            table = next(
                item
                for item in declared_visuals
                if finding.id in item["finding_ids"] and item["kind"] == "table"
            ) if any(
                finding.id in item["finding_ids"] and item["kind"] == "table"
                for item in declared_visuals
            ) else None
            claim_ids = [claim.claim_id for claim in finding.claims]
            blocks = [
                ReportEditorNarrativeBlock(
                    type="narrative",
                    text=_finding_narrative(finding),
                    purpose=finding.title,
                    claim_ids=claim_ids,
                )
            ]
            if reportable and finding.id == reportable[0].id and kpis:
                blocks.insert(
                    0,
                    ReportEditorKpiGridBlock(
                        type="kpi_grid",
                        metric_refs=[item.metric_ref for item in kpis],
                        presentation_role="overview",
                    ),
                )
            if visual:
                blocks.append(visual["visual"])
            if table:
                blocks.append(table["table"])
            sections.append(
                ReportEditorSection(
                    title=finding.title,
                    finding_refs=[finding.id],
                    claim_ids=claim_ids,
                    blocks=blocks,
                )
            )
        if not sections:
            sections = [
                ReportEditorSection(
                    title=headline[:240],
                    blocks=[
                        ReportEditorNarrativeBlock(
                            type="narrative",
                            text=inputs.findings.summary,
                            purpose="report summary",
                        )
                    ],
                    finding_refs=[],
                )
            ]
        return ReportEditorSpec(
            headline=headline[:300],
            summary=(inputs.findings.summary or headline)[:2000],
            kpis=kpis,
            sections=sections,
        )


def _declared_visuals(inputs: ReportInputs) -> list[dict]:
    catalog = {entry.path: entry for entry in inputs.catalog}
    visuals: list[dict] = []
    for artifact in inputs.evidence_manifest.artifacts:
        entry = catalog.get(artifact.artifact_path)
        if entry is None or artifact.usage in {"none", "evidence_only"}:
            continue
        if artifact.chart is not None:
            visuals.append(
                {
                    "kind": "chart",
                    "finding_ids": list(artifact.finding_ids),
                    "visual": ReportEditorChartBlock(
                        type="chart",
                        data_ref=artifact.artifact_path,
                        chart_type=artifact.chart.chart_type,
                        x_field=artifact.chart.x_field,
                        series=[item.field for item in artifact.chart.series],
                        title=artifact.chart.title,
                        purpose=artifact.purpose or artifact.chart.title,
                    ),
                }
            )
        if artifact.table is not None:
            visuals.append(
                {
                    "kind": "table",
                    "finding_ids": list(artifact.finding_ids),
                    "table": ReportEditorTableBlock(
                        data_ref=artifact.artifact_path,
                        columns=[item.field for item in artifact.table.columns],
                        title=artifact.table.title,
                        purpose=artifact.purpose or artifact.table.title,
                    ),
                }
            )
    return visuals


def _finding_narrative(finding) -> str:
    evidence = "；".join(item.strip() for item in finding.evidence if item.strip())
    parts = [finding.title]
    if evidence:
        parts.append(evidence)
    if finding.risk.strip():
        parts.append(finding.risk.strip())
    return " ".join(parts)[:4000]
