"""Deterministic scalar Metric provenance checks at complete_analysis packaging."""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.services.artifact_schema import ArtifactSchemaInspector
from app.services.metric_contract import MetricDefinition
from app.services.report_ready_artifacts import ReportReadyArtifact
from app.services.workspace import PathResolver

_MISSING = object()


def validate_metric_provenance(
    resolver: PathResolver,
    project_id: str,
    findings: Iterable[Any],
    metrics: Iterable[MetricDefinition],
    report_ready_artifacts: Iterable[ReportReadyArtifact],
) -> list[dict[str, Any]]:
    """Verify quantitative scalar evidence directly associated with report-ready data."""

    registry = {metric.metric_id: metric for metric in metrics}
    report_ready = {item.artifact_path: item for item in report_ready_artifacts}
    issues: list[dict[str, Any]] = []
    for finding in findings:
        for claim in finding.claims:
            if not claim.is_quantitative:
                continue
            related_paths = [
                path
                for path in dict.fromkeys(claim.evidence_artifact_paths)
                if path in report_ready
            ]
            if not related_paths:
                continue
            for metric_id in claim.evidence_metric_ids:
                metric = registry.get(metric_id)
                if metric is None or metric.metric_scope != "scalar_evidence":
                    continue
                base = {
                    "metric_id": metric.metric_id,
                    "finding_id": finding.id,
                    "claim_id": claim.claim_id,
                    "source_artifact": metric.source_artifact,
                    "related_artifacts": related_paths,
                    "expected_artifact": metric.source_artifact,
                    "actual_artifacts": related_paths,
                    "declared_value": metric.value,
                    "metric_grain": metric.grain,
                    **_source_context(resolver.resolve(project_id, metric.source_artifact), metric),
                }
                if metric.source_artifact not in related_paths:
                    issues.append(
                        {
                            "code": "METRIC_PROVENANCE_ARTIFACT_MISMATCH",
                            **base,
                        }
                    )
                    continue
                expected_grains = (
                    _artifact_measure_grains(report_ready[metric.source_artifact], registry)
                    if metric.source_artifact in report_ready
                    else set()
                )
                if (
                    metric.grain is not None
                    and expected_grains
                    and metric.grain not in expected_grains
                ):
                    issues.append(
                        {
                            "code": "METRIC_PROVENANCE_GRAIN_MISMATCH",
                            **base,
                            "metric_grain": metric.grain,
                            "artifact_grains": sorted(expected_grains),
                        }
                    )
                    continue
                reproduced = _reproduce_scalar(
                    resolver.resolve(project_id, metric.source_artifact), metric
                )
                if reproduced is None:
                    issues.append(
                        {
                            "code": "METRIC_PROVENANCE_UNVERIFIABLE",
                            **base,
                            "source_field": metric.source_field,
                            "source_selector": metric.source_selector,
                        }
                    )
                    continue
                allowed = max(1e-9, abs(reproduced) * metric.tolerance)
                if not math.isclose(metric.value, reproduced, abs_tol=allowed, rel_tol=0):
                    issues.append(
                        {
                            "code": "METRIC_PROVENANCE_VALUE_MISMATCH",
                            **base,
                            "source_field": metric.source_field,
                            "source_selector": metric.source_selector,
                            "declared_value": metric.value,
                            "reproduced_value": reproduced,
                        }
                    )
    return issues


def validate_scalar_artifact_contract(
    resolver: PathResolver,
    project_id: str,
    contract: Any,
) -> list[dict[str, Any]]:
    """Validate creation-time scalar declarations against the generated Artifact."""

    path = resolver.resolve(project_id, contract.artifact_path)
    issues: list[dict[str, Any]] = []
    if not path.is_file():
        return [
            {
                "code": "SCALAR_ARTIFACT_MISSING",
                "artifact_path": contract.artifact_path,
            }
        ]
    if path.suffix.lower() != ".json":
        return [
            {
                "code": "SCALAR_ARTIFACT_UNSUPPORTED",
                "artifact_path": contract.artifact_path,
                "artifact_kind": path.suffix.lower().lstrip(".") or "unknown",
            }
        ]
    structure = ArtifactSchemaInspector().inspect(path) or {}
    available_paths = {
        ".".join(str(segment) for segment in item)
        for item in structure.get("value_paths", [])
        if isinstance(item, list)
    }
    available_fields = {
        item.get("name")
        for item in structure.get("fields", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for metric in contract.metrics:
        source_field = metric.source_field
        if source_field is None or (
            source_field not in available_paths and source_field not in available_fields
        ):
            issues.append(
                {
                    "code": "SCALAR_METRIC_SOURCE_FIELD_MISSING",
                    "artifact_path": contract.artifact_path,
                    "metric_id": metric.metric_id,
                    "source_field": source_field,
                    "available_fields": sorted(available_fields),
                    "available_value_paths": sorted(available_paths),
                }
            )
            continue
        reproduced = _reproduce_scalar(path, metric)
        if reproduced is None:
            issues.append(
                {
                    "code": "SCALAR_METRIC_VALUE_UNVERIFIABLE",
                    "artifact_path": contract.artifact_path,
                    "metric_id": metric.metric_id,
                    "source_field": source_field,
                    "source_selector": metric.source_selector,
                }
            )
            continue
        allowed = max(1e-9, abs(reproduced) * metric.tolerance)
        if not math.isclose(metric.value, reproduced, abs_tol=allowed, rel_tol=0):
            issues.append(
                {
                    "code": "SCALAR_METRIC_VALUE_MISMATCH",
                    "artifact_path": contract.artifact_path,
                    "metric_id": metric.metric_id,
                    "source_field": source_field,
                    "declared_value": metric.value,
                    "observed_value": reproduced,
                }
            )
    return issues


def _artifact_measure_grains(
    artifact: ReportReadyArtifact,
    metrics: dict[str, MetricDefinition],
) -> set[str]:
    return {
        metric.grain
        for field in artifact.fields
        if field.role == "measure"
        and field.metric_ref in metrics
        and (metric := metrics[field.metric_ref]).grain is not None
    }


def _source_context(path: Path, metric: MetricDefinition) -> dict[str, Any]:
    structure = ArtifactSchemaInspector().inspect(path) if path.is_file() else None
    context: dict[str, Any] = {}
    if isinstance(structure, dict):
        fields = structure.get("fields")
        if isinstance(fields, list):
            context["available_fields"] = [
                item.get("name")
                for item in fields[:60]
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            ]
        value_paths = structure.get("value_paths")
        if isinstance(value_paths, list):
            context["available_value_paths"] = [
                ".".join(str(segment) for segment in item)
                for item in value_paths[:60]
                if isinstance(item, list)
            ]
    observed = _reproduce_scalar(path, metric)
    if observed is not None:
        context["observed_value"] = observed
    return context


def _reproduce_scalar(path: Path, metric: MetricDefinition) -> float | None:
    if metric.source_field is None or not path.is_file():
        return None
    object_value = _object_scalar(path, metric.source_field)
    if object_value is not _MISSING:
        if metric.source_selector:
            return None
        return _number(object_value)
    records = _records(path)
    if records is None:
        return None
    selector = metric.source_selector or {}
    selected = [row for row in records if _matches(row, selector)]
    if not selected or (len(records) > 1 and not selector):
        return None
    values = [_number(row.get(metric.source_field)) for row in selected]
    if any(value is None for value in values):
        return None
    numeric = [value for value in values if value is not None]
    if len(numeric) == 1:
        return numeric[0]
    aggregation = metric.aggregation.lower()
    if aggregation in {"sum", "field_sum"}:
        return sum(numeric)
    if aggregation in {"mean", "average", "avg"}:
        return sum(numeric) / len(numeric)
    if aggregation == "min":
        return min(numeric)
    if aggregation == "max":
        return max(numeric)
    if aggregation in {"count", "row_count"}:
        return float(len(numeric))
    return None


def _object_scalar(path: Path, field_path: str) -> Any:
    if path.suffix.lower() != ".json":
        return _MISSING
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _MISSING
    if not isinstance(payload, dict):
        return _MISSING
    if field_path in payload:
        value = payload[field_path]
        return value if not isinstance(value, (dict, list)) else _MISSING
    value: Any = payload
    for segment in field_path.split("."):
        if isinstance(value, dict) and segment in value:
            value = value[segment]
        else:
            return _MISSING
    return value if not isinstance(value, (dict, list)) else _MISSING


def _records(path: Path) -> list[dict[str, Any]] | None:
    try:
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload = next(
                    (
                        payload[key]
                        for key in ("records", "data")
                        if isinstance(payload.get(key), list)
                    ),
                    payload,
                )
            if isinstance(payload, list) and all(isinstance(row, dict) for row in payload):
                return payload
    except (OSError, UnicodeDecodeError, csv.Error, json.JSONDecodeError):
        return None
    return None


def _matches(row: dict[str, Any], selector: dict[str, Any]) -> bool:
    return all(
        str(row.get(field, "")).strip() == str(value).strip() for field, value in selector.items()
    )


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").rstrip("%"))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
