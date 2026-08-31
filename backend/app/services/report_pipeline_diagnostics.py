"""Small structural diagnostics for the Report pipeline."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app.services.report_editor_spec import (
    ReportEditorChartBlock,
    ReportEditorNarrativeBlock,
    ReportEditorSpec,
    ReportEditorTableBlock,
    ReportEditorVisualGroupBlock,
)
from app.services.report_metric_fidelity import eligible_visual_contexts
from app.services.report_spec import (
    ChartBlock,
    NarrativeBlock,
    ReportSpec,
    TableBlock,
    VisualGroupBlock,
)


def input_counts(inputs: Any) -> dict[str, Any]:
    eligible = eligible_visual_contexts(inputs)
    return {
        "metrics": len(inputs.metrics),
        "artifacts": len(inputs.catalog),
        "report_ready_artifacts": sum(item.report_ready for item in inputs.catalog),
        "eligible_visuals": len(eligible),
        "eligible_charts": sum(item["visual_type"] == "chart" for item in eligible),
        "eligible_tables": sum(item["visual_type"] == "table" for item in eligible),
    }


def editor_counts(spec: ReportEditorSpec) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    for section in spec.sections:
        roles[section.section_role or "balanced"] += 1
        for block in section.blocks:
            _count_editor_block(block, counts)
    return _result(counts, roles, len(spec.sections), len(spec.kpis))


def report_counts(spec: ReportSpec) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    for section in spec.sections:
        roles[section.visual_strategy] += 1
        for block in section.blocks:
            _count_report_block(block, counts)
    return _result(counts, roles, len(spec.sections), len(spec.kpis))


def analytical_visual_count(spec: ReportSpec) -> int:
    counts = report_counts(spec)
    return int(counts["charts"]) + int(counts["tables"])


def log_diagnostics(logger: Any, stage: str, counts: dict[str, Any], **details: Any) -> None:
    payload = {"stage": stage, **counts, **details}
    logger.info(
        "report_pipeline_diagnostics %s",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )


def _count_editor_block(block: Any, counts: Counter[str]) -> None:
    if isinstance(block, ReportEditorChartBlock):
        counts["charts"] += 1
    elif isinstance(block, ReportEditorTableBlock):
        counts["tables"] += 1
    elif isinstance(block, ReportEditorVisualGroupBlock):
        counts["visual_groups"] += 1
        for item in block.items:
            _count_editor_block(item, counts)
    elif isinstance(block, ReportEditorNarrativeBlock):
        counts["narratives"] += 1
        if block.display_role == "evidence_interpretation":
            counts["evidence_interpretations"] += 1


def _count_report_block(block: Any, counts: Counter[str]) -> None:
    if isinstance(block, ChartBlock):
        if block.chart.visual_purpose == "analytical":
            counts["charts"] += 1
    elif isinstance(block, TableBlock):
        counts["tables"] += 1
    elif isinstance(block, VisualGroupBlock):
        counts["visual_groups"] += 1
        for item in block.items:
            _count_report_block(item, counts)
    elif isinstance(block, NarrativeBlock):
        counts["narratives"] += 1
        if block.display_role == "evidence_interpretation":
            counts["evidence_interpretations"] += 1


def _result(counts: Counter[str], roles: Counter[str], sections: int, kpis: int) -> dict[str, Any]:
    return {
        "sections": sections,
        "kpis": kpis,
        "charts": counts["charts"],
        "tables": counts["tables"],
        "visual_groups": counts["visual_groups"],
        "evidence_interpretations": counts["evidence_interpretations"],
        "narratives": counts["narratives"],
        "section_roles": dict(sorted(roles.items())),
    }
