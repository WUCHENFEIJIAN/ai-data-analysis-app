"""Small helpers for keeping visual and narrative metric references aligned."""

from __future__ import annotations

from typing import Any

from app.services.report_editor_spec import (
    ReportEditorChartBlock,
    ReportEditorTableBlock,
    ReportEditorVisualGroupBlock,
)
from app.services.report_inputs import ReportInputs


def build_visual_context(inputs: ReportInputs) -> list[dict[str, Any]]:
    """Expose declared visual field/metric metadata to the Report Editor."""

    metrics = {item.metric_id: item for item in inputs.metrics}
    catalog = {entry.path: entry for entry in inputs.catalog}
    declared = {
        item.artifact_path: item
        for item in inputs.evidence_manifest.artifacts
        if item.chart is not None or item.table is not None
    }
    visuals: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for path, evidence in declared.items():
        entry = catalog.get(path)
        if entry is None or not entry.report_ready:
            continue
        if evidence.chart is not None:
            context = _chart_context(path, evidence.chart, entry, metrics)
            if _visual_contract_ready(context):
                visuals.append(context)
                seen.add((path, "chart"))
        if evidence.table is not None:
            context = _table_context(path, evidence.table, entry, metrics)
            if _visual_contract_ready(context):
                visuals.append(context)
                seen.add((path, "table"))

    for entry in inputs.catalog:
        if entry.kind not in {"csv", "json"} or not entry.report_ready:
            continue
        fields = _fallback_fields(entry, metrics)
        dimension = next(
            (item["field_ref"] for item in fields if item.get("role") == "dimension"),
            None,
        )
        for kind in ("chart", "table"):
            if (entry.path, kind) in seen:
                continue
            series = (
                [item for item in fields if item.get("role") == "measure"]
                if kind == "chart"
                else fields
            )
            context = {
                "data_ref": entry.path,
                "visual_type": kind,
                "title": entry.path,
                "dimension": dimension,
                "report_ready": True,
                "metric_refs": _unique(item["metric_ref"] for item in series if item["metric_ref"]),
                "series": series,
            }
            if _visual_contract_ready(context):
                visuals.append(context)
    return visuals


def eligible_visual_contexts(inputs: ReportInputs) -> list[dict[str, Any]]:
    """Return analytical contexts with explicit business-evidence support.

    Visual eligibility has two independent requirements: ``build_visual_context`` must prove
    the Artifact and reusable metric contract, and a reportable business Claim must explicitly
    bind the Artifact either by path or through a referenced MetricDefinition source artifact.
    Claim text being numeric is not a prerequisite for using a structurally valid Artifact as
    visual evidence.
    """

    supported_paths = _supported_visual_artifact_paths(inputs)
    return [
        context
        for context in build_visual_context(inputs)
        if context.get("data_ref") in supported_paths
    ]


def _supported_visual_artifact_paths(inputs: ReportInputs) -> set[str]:
    """Resolve visual support from claim-level evidence, never finding-level adjacency."""

    from app.services.report_reportability import business_findings_for_report

    metrics = {item.metric_id: item for item in inputs.metrics}
    supported_paths: set[str] = set()
    for finding in business_findings_for_report(inputs.findings):
        for claim in finding.claims:
            referenced_metrics = [metrics.get(metric_id) for metric_id in claim.evidence_metric_ids]
            if claim.is_quantitative and (
                not claim.evidence_metric_ids
                or any(metric is None for metric in referenced_metrics)
            ):
                # Quantitative Claim provenance remains strict; an Artifact alone cannot repair
                # a missing or invalid metric binding.
                continue
            supported_paths.update(path for path in claim.evidence_artifact_paths if path)
            supported_paths.update(
                metric.source_artifact
                for metric in referenced_metrics
                if metric is not None and metric.source_artifact
            )
    return supported_paths
def eligible_visual_context_keys(inputs: ReportInputs) -> set[tuple[str, str]]:
    """Return accepted artifact/type pairs the Report Editor may use analytically."""

    return {(item["data_ref"], item["visual_type"]) for item in eligible_visual_contexts(inputs)}


def metric_definition_for_field(data_ref: str, field_ref: str, inputs: ReportInputs) -> Any | None:
    """Resolve a declared artifact field to its canonical MetricDefinition.

    The artifact declaration is the authoritative field-to-metric binding when a
    metric id differs from the physical column name.  Schema annotations and an
    exact ``metric_id == field`` match are compatibility fallbacks only.
    """

    metrics = {item.metric_id: item for item in inputs.metrics}
    for artifact in inputs.evidence_manifest.artifacts:
        if artifact.artifact_path != data_ref:
            continue
        declarations = []
        if artifact.chart is not None:
            declarations.extend(artifact.chart.series)
        if artifact.table is not None:
            declarations.extend(artifact.table.columns)
        for declaration in declarations:
            if declaration.field == field_ref and declaration.metric:
                metric = metrics.get(declaration.metric)
                if metric is not None:
                    return metric

    entry = next((item for item in inputs.catalog if item.path == data_ref), None)
    if entry is not None:
        for column in (entry.structure or {}).get("columns", []):
            if column.get("name") == field_ref:
                metric_ref = column.get("metric_ref") or column.get("metric_id")
                if metric_ref in metrics:
                    return metrics[metric_ref]

    entry = next((item for item in inputs.catalog if item.path == data_ref), None)
    quantitative_fields = [
        column.get("name")
        for column in ((entry.structure or {}).get("columns", []) if entry else [])
        if column.get("name")
        and str(column.get("semantic_type") or "").lower()
        not in {"text", "identifier", "date", "datetime"}
        and str(column.get("type") or column.get("dtype") or "").lower()
        in {"number", "integer", "float", "decimal"}
    ]
    candidates = [item for item in metrics.values() if item.source_artifact == data_ref]
    if len(candidates) == 1 and quantitative_fields == [field_ref]:
        return candidates[0]

    metric = metrics.get(field_ref)
    if metric is not None:
        return metric
    return next(
        (
            item
            for item in metrics.values()
            if item.source_artifact == data_ref and item.metric_id == field_ref
        ),
        None,
    )


def visual_metric_refs(section: Any, data_ref: str, inputs: ReportInputs) -> set[str]:
    """Return metric ids represented by a visual in one editor section."""

    contexts = [item for item in build_visual_context(inputs) if item["data_ref"] == data_ref]
    refs: set[str] = set()
    for block in section.blocks:
        for visual in _visual_blocks(block):
            if visual.data_ref != data_ref:
                continue
            fields = visual.series if isinstance(visual, ReportEditorChartBlock) else visual.columns
            context = next(
                (
                    item
                    for item in contexts
                    if item["visual_type"]
                    == ("chart" if isinstance(visual, ReportEditorChartBlock) else "table")
                ),
                None,
            )
            if context is not None:
                field_refs = set(fields)
                refs.update(
                    item["metric_ref"]
                    for item in context["series"]
                    if item["field_ref"] in field_refs and item["metric_ref"]
                )
            else:
                refs.update(field for field in fields if field)
    return refs


def _chart_context(path: str, chart: Any, entry: Any, metrics: dict[str, Any]) -> dict[str, Any]:
    series = [
        _series_context(item.field, item.metric, item.label, metrics, entry)
        for item in chart.series
    ]
    return {
        "data_ref": path,
        "visual_type": "chart",
        "title": chart.title,
        "dimension": chart.x_field,
        "report_ready": entry.report_ready,
        "dimension_declared": _field_role(entry, chart.x_field) == "dimension",
        "metric_refs": _unique(item["metric_ref"] for item in series if item["metric_ref"]),
        "series": series,
    }


def _table_context(path: str, table: Any, entry: Any, metrics: dict[str, Any]) -> dict[str, Any]:
    series = [
        _series_context(item.field, item.metric, item.label, metrics, entry)
        for item in table.columns
    ]
    return {
        "data_ref": path,
        "visual_type": "table",
        "title": table.title,
        "dimension": next(
            (item["field_ref"] for item in series if item.get("role") == "dimension"),
            None,
        ),
        "report_ready": entry.report_ready,
        "metric_refs": _unique(item["metric_ref"] for item in series if item["metric_ref"]),
        "series": series,
    }


def _series_context(
    field_ref: str,
    declared_metric: str | None,
    label: str | None,
    metrics: dict[str, Any],
    entry: Any,
) -> dict[str, Any]:
    column = next(
        (
            item
            for item in (entry.structure or {}).get("columns", [])
            if item.get("name") == field_ref
        ),
        {},
    )
    metric = metrics.get(declared_metric or "")
    if metric is None:
        metric = next(
            (
                item
                for item in metrics.values()
                if item.source_artifact == entry.path and item.metric_id == field_ref
            ),
            None,
        )
    return {
        "field_ref": field_ref,
        "metric_ref": metric.metric_id if metric is not None else declared_metric,
        "display_label": label or (metric.label if metric is not None else field_ref),
        "aggregation": metric.aggregation if metric is not None else None,
        "count_semantics": metric.count_semantics if metric is not None else None,
        "grain": metric.grain if metric is not None else None,
        "role": column.get("role"),
        "presentation_usable": column.get("presentation_usable", True),
    }


def _fallback_fields(entry: Any, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    fields = []
    for column in (entry.structure or {}).get("columns", []):
        field = column.get("name")
        role = column.get("role")
        if not field or role not in {"dimension", "measure"}:
            continue
        declared_metric = column.get("metric_ref") or column.get("metric_id")
        metric = metrics.get(declared_metric or "")
        if metric is None:
            metric = next(
                (
                    item
                    for item in metrics.values()
                    if item.source_artifact == entry.path and item.metric_id == field
                ),
                None,
            )
        fields.append(
            {
                "field_ref": field,
                "metric_ref": metric.metric_id if metric is not None else declared_metric,
                "display_label": (
                    column.get("display_label") or (metric.label if metric is not None else field)
                ),
                "aggregation": metric.aggregation if metric is not None else None,
                "count_semantics": metric.count_semantics if metric is not None else None,
                "grain": getattr(metric, "grain", None) if metric is not None else None,
                "role": role,
                "presentation_usable": column.get("presentation_usable", True),
            }
        )
    return fields


def _field_role(entry: Any, field_ref: str) -> str | None:
    return next(
        (
            item.get("role")
            for item in (entry.structure or {}).get("columns", [])
            if item.get("name") == field_ref
        ),
        None,
    )


def _visual_contract_ready(context: dict[str, Any]) -> bool:
    if not context.get("report_ready") or not context.get("dimension"):
        return False
    if context.get("visual_type") == "chart" and not context.get("dimension_declared", True):
        return False
    measures = [
        item
        for item in context.get("series", [])
        if item.get("role") == "measure"
        and item.get("presentation_usable", True)
        and item.get("metric_ref")
    ]
    return bool(measures)


def _visual_blocks(block: Any) -> list[Any]:
    if isinstance(block, (ReportEditorChartBlock, ReportEditorTableBlock)):
        return [block]
    if isinstance(block, ReportEditorVisualGroupBlock):
        return [item for child in block.items for item in _visual_blocks(child)]
    return []


def _unique(values: Any) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
