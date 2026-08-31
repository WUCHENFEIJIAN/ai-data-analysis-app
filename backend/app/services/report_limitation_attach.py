from __future__ import annotations

from typing import Any

from app.services.report_editor_spec import (
    ReportEditorChartBlock,
    ReportEditorKpiGridBlock,
    ReportEditorNarrativeBlock,
    ReportEditorSpec,
    ReportEditorTableBlock,
    ReportEditorVisualGroupBlock,
)
from app.services.report_inputs import ReportInputs
from app.services.report_reportability import (
    business_findings_for_report,
    limitation_items,
)


def attach_report_limitations(spec: ReportEditorSpec, inputs: ReportInputs) -> ReportEditorSpec:
    items = limitation_items(inputs.findings)
    if not items or not spec.sections:
        return spec
    business_ids = {finding.id for finding in business_findings_for_report(inputs.findings)}
    existing = _existing_limitation_keys(spec)
    sections = list(spec.sections)
    summary = spec.summary
    for item in items:
        key = _limitation_key(item)
        if key in existing:
            continue
        matches = [
            index
            for index, section in enumerate(sections)
            if _section_matches_limitation(section, item, business_ids)
        ]
        if not matches:
            if item.get("severe"):
                summary = _append_summary_warning(summary, item["statement"])
            continue
        if len(matches) > 1 or item.get("severe"):
            summary = _append_summary_warning(summary, item["statement"])
        block = _limitation_block(item)
        for index in matches:
            section = sections[index]
            sections[index] = section.model_copy(update={"blocks": [*section.blocks, block]})
        existing.add(key)
    if sections == list(spec.sections) and summary == spec.summary:
        return spec
    return spec.model_copy(update={"summary": summary, "sections": sections})


def _limitation_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("claim_id") or item.get("finding_id") or ""), item["statement"])


def _existing_limitation_keys(spec: ReportEditorSpec) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for section in spec.sections:
        for block in section.blocks:
            if not isinstance(block, ReportEditorNarrativeBlock):
                continue
            if block.display_role != "limitation":
                continue
            claim_id = block.claim_ids[0] if block.claim_ids else ""
            keys.add((claim_id, block.text))
            keys.add(("", block.text))
    return keys


def _section_matches_limitation(
    section: Any,
    item: dict[str, Any],
    business_ids: set[str],
) -> bool:
    finding_ids, claim_ids, metric_ids, artifact_paths = _section_refs(section)
    related_findings = set(item.get("related_finding_ids") or [])
    related_findings.add(str(item.get("finding_id") or ""))
    related_findings.discard("")
    related_metrics = set(item.get("related_metric_refs") or item.get("evidence_metric_ids") or [])
    related_artifacts = set(item.get("evidence_artifact_paths") or [])
    if claim_ids & {str(item.get("claim_id") or "")}:
        return True
    if finding_ids & related_findings:
        if finding_ids & business_ids or not business_ids:
            return True
        if artifact_paths & related_artifacts or metric_ids & related_metrics:
            return True
        return False
    if artifact_paths & related_artifacts:
        return True
    if metric_ids & related_metrics:
        return True
    return False


def _section_refs(section: Any) -> tuple[set[str], set[str], set[str], set[str]]:
    finding_ids = set(section.finding_refs)
    claim_ids = set(section.claim_ids)
    metric_ids: set[str] = set()
    artifact_paths: set[str] = set()
    for block in section.blocks:
        _collect_block_refs(block, claim_ids, metric_ids, artifact_paths)
    return finding_ids, claim_ids, metric_ids, artifact_paths


def _collect_block_refs(
    block: Any,
    claim_ids: set[str],
    metric_ids: set[str],
    artifact_paths: set[str],
) -> None:
    if isinstance(block, ReportEditorNarrativeBlock):
        claim_ids.update(block.claim_ids)
        metric_ids.update(block.metric_refs)
    elif isinstance(block, ReportEditorKpiGridBlock):
        metric_ids.update(block.metric_refs)
    elif isinstance(block, ReportEditorChartBlock):
        artifact_paths.add(block.data_ref)
        metric_ids.update(block.series)
    elif isinstance(block, ReportEditorTableBlock):
        artifact_paths.add(block.data_ref)
        metric_ids.update(block.columns)
    elif isinstance(block, ReportEditorVisualGroupBlock):
        for item in block.items:
            _collect_block_refs(item, claim_ids, metric_ids, artifact_paths)


def _limitation_block(item: dict[str, Any]) -> ReportEditorNarrativeBlock:
    claim_id = str(item.get("claim_id") or "")
    metric_refs = list(item.get("related_metric_refs") or item.get("evidence_metric_ids") or [])
    return ReportEditorNarrativeBlock(
        type="narrative",
        text=item["statement"][:4000],
        claim_ids=[claim_id] if claim_id else [],
        purpose="report limitation",
        display_role="limitation",
        metric_refs=metric_refs[:12],
    )


def _append_summary_warning(summary: str, statement: str) -> str:
    text = statement.strip()
    if not text or text in summary:
        return summary
    combined = f"{summary.rstrip()} {text}".strip()
    return combined[:2000]
