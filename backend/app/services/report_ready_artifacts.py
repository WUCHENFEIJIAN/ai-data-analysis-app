"""Canonical field bindings for report-ready analytical Artifacts.

Metric definitions remain in ``analysis/metrics.json``.  These declarations only
connect physical Artifact fields to that registry; they are not a second metric
registry or a visual plan.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.artifact_schema import ArtifactSchemaInspector
from app.services.metric_contract import MetricDefinition
from app.services.workspace import PathResolver


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportReadyField(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    role: Literal["dimension", "measure", "context"]
    metric_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description=(
            "Canonical metrics.json metric_id for a measure field. Dimensions must omit it."
        ),
    )
    presentation_usable: bool = True

    @model_validator(mode="after")
    def validate_dimension(self) -> ReportReadyField:
        if self.role != "measure" and self.metric_ref is not None:
            raise ValueError("only measure fields may declare metric_ref")
        return self


class ReportReadyArtifact(StrictModel):
    artifact_path: str = Field(
        min_length=1,
        max_length=300,
        description="Existing report-ready CSV or tabular JSON path under data/.",
    )
    origin_task_id: str | None = Field(
        default=None,
        pattern=r"^task_[A-Za-z0-9_-]+$",
        description="Analysis task that created and declared this Artifact.",
    )
    grain: str | None = Field(default=None, min_length=1, max_length=120)
    fields: list[ReportReadyField] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def validate_unique_fields(self) -> ReportReadyArtifact:
        names = [item.name for item in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("report-ready artifact fields must be unique")
        return self


class AnalysisArtifactContract(ReportReadyArtifact):
    """Creation-time semantic declaration submitted with one Analysis Action."""

    origin_task_id: str | None = Field(default=None, pattern=r"^task_[A-Za-z0-9_-]+$")
    grain: str = Field(min_length=1, max_length=120)
    metrics: list[MetricDefinition] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_local_closure(self) -> AnalysisArtifactContract:
        metric_ids = [metric.metric_id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("artifact contract metrics must have unique metric_id values")
        registry = {metric.metric_id: metric for metric in self.metrics}
        if not any(field.role == "dimension" for field in self.fields):
            raise ValueError("artifact contract requires a dimension field")
        usable_measures = [
            field for field in self.fields if field.role == "measure" and field.presentation_usable
        ]
        if not usable_measures:
            raise ValueError("artifact contract requires a presentation-usable measure")
        for field in usable_measures:
            if not field.metric_ref or field.metric_ref not in registry:
                raise ValueError(
                    f"measure {field.name} must reference a metric declared in this contract"
                )
            metric = registry[field.metric_ref]
            if metric.metric_scope != "reusable_measure":
                raise ValueError(f"measure {field.name} must reference a reusable_measure metric")
            if metric.source_artifact != self.artifact_path:
                raise ValueError(
                    f"metric {metric.metric_id} source_artifact must match artifact_path"
                )
            if metric.source_field != field.name:
                raise ValueError(f"metric {metric.metric_id} source_field must match measure field")
            if metric.grain != self.grain:
                raise ValueError(
                    f"metric {metric.metric_id} grain {metric.grain!r} must match artifact grain "
                    f"{self.grain!r}; dataset-level scalar values belong in "
                    "scalar_artifact_contracts, not artifact_contracts"
                )
        field_by_name = {field.name: field for field in self.fields}
        for metric in self.metrics:
            if metric.metric_scope != "reusable_measure" or metric.aggregation.lower() != "ratio":
                continue
            numerator = registry.get(metric.numerator or "")
            denominator = registry.get(metric.denominator or "")
            if numerator is None or denominator is None:
                continue
            for label, referenced in (("numerator", numerator), ("denominator", denominator)):
                if referenced.source_artifact != self.artifact_path:
                    raise ValueError(
                        f"reusable ratio {metric.metric_id} {label} source_artifact must match "
                        "artifact_path"
                    )
                if referenced.grain != self.grain:
                    raise ValueError(
                        f"reusable ratio {metric.metric_id} {label} grain must match artifact grain"
                    )
                if not referenced.source_field:
                    raise ValueError(
                        f"reusable ratio {metric.metric_id} {label} must declare source_field"
                    )
                source_field = field_by_name.get(referenced.source_field)
                if source_field is None or source_field.role != "measure":
                    raise ValueError(
                        f"reusable ratio {metric.metric_id} {label} source_field must be a "
                        "measure field in the contract"
                    )
            ratio_field = field_by_name.get(metric.source_field or "")
            if ratio_field is None or ratio_field.role != "measure":
                raise ValueError(
                    f"reusable ratio {metric.metric_id} source_field must be a measure field "
                    "in the contract"
                )
        return self

    def report_ready_declaration(self) -> ReportReadyArtifact:
        if self.origin_task_id is None:
            raise ValueError("artifact contract origin_task_id must be assigned by the application")
        return ReportReadyArtifact.model_validate(self.model_dump(mode="json", exclude={"metrics"}))


class ScalarArtifactContract(StrictModel):
    """Creation-time declaration for scalar values stored in a JSON Artifact.

    Scalar evidence is deliberately separate from report-ready table contracts:
    it proves one dataset-level observation without making a field eligible for
    dimensional charts or tables.
    """

    artifact_path: str = Field(
        min_length=1,
        max_length=300,
        description="Existing or newly-created JSON Artifact containing scalar evidence.",
    )
    metrics: list[MetricDefinition] = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_scalar_metrics(self) -> ScalarArtifactContract:
        metric_ids = [metric.metric_id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("scalar artifact contract metrics must have unique metric_id values")
        for metric in self.metrics:
            if metric.metric_scope != "scalar_evidence":
                raise ValueError(
                    "scalar artifact contracts may contain only scalar_evidence metrics"
                )
            if metric.source_artifact != self.artifact_path:
                raise ValueError(
                    f"metric {metric.metric_id} source_artifact must match artifact_path"
                )
            if not metric.source_field:
                raise ValueError(f"scalar metric {metric.metric_id} must declare source_field")
        return self


def validate_report_ready_artifacts(
    resolver: PathResolver,
    project_id: str,
    declarations: Iterable[ReportReadyArtifact],
    metrics: Iterable[MetricDefinition],
) -> list[dict[str, Any]]:
    """Return deterministic complete_analysis validation issues."""

    metric_list = list(metrics)
    registry = {item.metric_id: item for item in metric_list}
    issues: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    inspector = ArtifactSchemaInspector()
    for declaration in declarations:
        path = declaration.artifact_path
        if path in seen_paths:
            issues.append(
                {
                    "code": "REPORT_READY_ARTIFACT_DUPLICATE",
                    "artifact_path": path,
                }
            )
            continue
        seen_paths.add(path)
        target = resolver.resolve(project_id, path)
        if not path.startswith("data/") or target.suffix.lower() not in {".csv", ".json"}:
            issues.append(
                {
                    "code": "REPORT_READY_ARTIFACT_UNSUPPORTED",
                    "artifact_path": path,
                    "artifact_kind": target.suffix.lower().lstrip(".") or "unknown",
                    "eligible_for_tabular_visual": False,
                }
            )
            continue
        if not target.is_file():
            issues.append(
                {
                    "code": "REPORT_READY_ARTIFACT_MISSING",
                    "artifact_path": path,
                }
            )
            continue
        structure = inspector.inspect(target) or {}
        physical_columns = {
            item.get("name"): item
            for item in structure.get("columns", [])
            if isinstance(item, dict) and item.get("name")
        }
        physical_fields = set(physical_columns)
        available_fields = list(physical_columns)
        records = _tabular_records(target)
        repair_metadata = _repair_metadata(
            path,
            structure,
            physical_columns,
            records=records,
            metrics=metric_list,
        )
        if structure.get("record_kind") != "table":
            issues.append(
                {
                    "code": "REPORT_READY_ARTIFACT_NOT_TABULAR",
                    "artifact_path": path,
                    "artifact_kind": (
                        f"{target.suffix.lower().lstrip('.')}/"
                        f"{structure.get('record_kind', 'unknown')}"
                    ),
                    "eligible_for_tabular_visual": False,
                }
            )
            continue
        if not any(field.role == "dimension" for field in declaration.fields):
            issues.append(
                {
                    "code": "REPORT_READY_DIMENSION_MISSING",
                    "artifact_path": path,
                    "available_fields": available_fields,
                }
            )
        if not any(
            field.role == "measure" and field.presentation_usable for field in declaration.fields
        ):
            issues.append(
                {
                    "code": "REPORT_READY_MEASURE_MISSING",
                    "artifact_path": path,
                    "available_fields": available_fields,
                    "current_declaration": declaration.model_dump(mode="json"),
                    **repair_metadata,
                }
            )
            if (
                not repair_metadata["eligible_measures"]
                and repair_metadata["physical_measure_fields"]
            ):
                issues.append(
                    {
                        "code": "REPORT_READY_REUSABLE_METRIC_MISSING",
                        "artifact_path": path,
                        "current_declaration": declaration.model_dump(mode="json"),
                        "available_fields": available_fields,
                        **repair_metadata,
                    }
                )
        declared_dimensions = [
            field.name
            for field in declaration.fields
            if field.role == "dimension" and field.name in physical_fields
        ]
        if records:
            complete_dimension_rows = sum(
                all(not _missing(row.get(field)) for field in declared_dimensions)
                for row in records
            )
            if 0 < complete_dimension_rows < len(records):
                issues.append(
                    {
                        "code": "REPORT_READY_GRAIN_MIXED",
                        "artifact_path": path,
                        "dimension_fields": declared_dimensions,
                        "row_count": len(records),
                        "complete_dimension_rows": complete_dimension_rows,
                    }
                )
            for dimension in declared_dimensions:
                values = [row.get(dimension) for row in records]
                present = [str(value).strip() for value in values if not _missing(value)]
                distinct = set(present)
                repeated_single_dimension = len(declared_dimensions) == 1 and len(distinct) < len(
                    present
                )
                if len(distinct) < 2 or repeated_single_dimension:
                    issues.append(
                        {
                            "code": "REPORT_READY_DIMENSION_NOT_DISCRIMINATIVE",
                            "artifact_path": path,
                            "field": dimension,
                            "row_count": len(records),
                            "non_null_count": len(present),
                            "distinct_count": len(distinct),
                        }
                    )
            measure_rows = {
                field.name: {
                    index for index, row in enumerate(records) if not _missing(row.get(field.name))
                }
                for field in declaration.fields
                if field.role == "measure"
                and field.presentation_usable
                and field.name in physical_fields
            }
            for field, rows in measure_rows.items():
                coverage = len(rows) / len(records)
                if coverage < 0.5:
                    issues.append(
                        {
                            "code": "REPORT_READY_MEASURE_COVERAGE_LOW",
                            "artifact_path": path,
                            "field": field,
                            "row_count": len(records),
                            "non_null_count": len(rows),
                            "non_null_coverage": coverage,
                        }
                    )
            for field in declaration.fields:
                if field.role != "measure" or not field.presentation_usable:
                    continue
                metric = registry.get(field.metric_ref or "")
                if metric is None or metric.metric_scope != "reusable_measure":
                    continue
                if metric.aggregation.lower() != "ratio":
                    continue
                denominator = registry.get(metric.denominator or "")
                if denominator is None or not denominator.source_field:
                    continue
                denominator_field = denominator.source_field
                if denominator_field not in physical_fields:
                    continue
                for row_index, row in enumerate(records):
                    denominator_value = _numeric_value(row.get(denominator_field))
                    if denominator_value != 0 or _missing(row.get(field.name)):
                        continue
                    issues.append(
                        {
                            "code": "REPORT_READY_RATIO_ZERO_DENOMINATOR_VALUE",
                            "artifact_path": path,
                            "field": field.name,
                            "row_index": row_index,
                            "denominator_field": denominator_field,
                            "denominator_value": denominator_value,
                            "ratio_value": row.get(field.name),
                            "message": (
                                "ratio values must be missing when the bound denominator is zero; "
                                "do not coerce a zero-denominator ratio to numeric zero"
                            ),
                        }
                    )

            if not _coverage_sets_compatible(list(measure_rows.values())):
                issues.append(
                    {
                        "code": "REPORT_READY_SERIES_COVERAGE_INCOMPATIBLE",
                        "artifact_path": path,
                        "series_non_null_counts": {
                            field: len(rows) for field, rows in measure_rows.items()
                        },
                        "row_count": len(records),
                    }
                )
        declared_measure_grains = {
            metric.grain
            for field in declaration.fields
            if field.role == "measure"
            and field.presentation_usable
            and field.metric_ref in registry
            and (metric := registry[field.metric_ref]).grain is not None
        }
        if len(declared_measure_grains) > 1:
            issues.append(
                {
                    "code": "REPORT_READY_MEASURE_GRAIN_MISMATCH",
                    "artifact_path": path,
                    "measure_grains": sorted(declared_measure_grains),
                }
            )
        for field in declaration.fields:
            if field.name not in physical_fields:
                issues.append(
                    {
                        "code": "REPORT_READY_FIELD_UNKNOWN",
                        "artifact_path": path,
                        "field": field.name,
                        "available_fields": available_fields,
                    }
                )
                continue
            if field.role != "measure" or not field.presentation_usable:
                continue
            column = physical_columns[field.name]
            field_type = str(column.get("type") or column.get("dtype") or "").lower()
            semantic_type = str(column.get("semantic_type") or "").lower()
            if field_type not in {
                "number",
                "integer",
                "float",
                "decimal",
            } and semantic_type not in {
                "integer",
                "decimal",
                "currency",
                "percentage_fraction",
                "percentage_points",
            }:
                issues.append(
                    {
                        "code": "REPORT_READY_MEASURE_NOT_QUANTITATIVE",
                        "artifact_path": path,
                        "field": field.name,
                        "field_schema": column,
                    }
                )
                continue
            if not field.metric_ref:
                issues.append(
                    {
                        "code": "REPORT_READY_MEASURE_UNBOUND",
                        "artifact_path": path,
                        "field": field.name,
                        "available_metric_ids": sorted(registry),
                    }
                )
            elif field.metric_ref not in registry:
                issues.append(
                    {
                        "code": "REPORT_READY_MEASURE_METRIC_UNKNOWN",
                        "artifact_path": path,
                        "field": field.name,
                        "metric_ref": field.metric_ref,
                        "available_metric_ids": sorted(registry),
                        **_binding_repair_metadata(
                            path, field.name, structure, physical_columns, records, metric_list
                        ),
                    }
                )
            elif registry[field.metric_ref].metric_scope != "reusable_measure":
                issues.append(
                    {
                        "code": "REPORT_READY_MEASURE_NOT_REUSABLE",
                        "artifact_path": path,
                        "field": field.name,
                        "metric_ref": field.metric_ref,
                        "metric_scope": registry[field.metric_ref].metric_scope,
                        "required_metric_scope": "reusable_measure",
                        **_binding_repair_metadata(
                            path, field.name, structure, physical_columns, records, metric_list
                        ),
                    }
                )
            else:
                compatibility = _metric_binding_compatibility(
                    path, field.name, registry[field.metric_ref], structure, physical_columns
                )
                if compatibility:
                    issues.append(
                        {
                            "code": "REPORT_READY_MEASURE_INCOMPATIBLE",
                            "artifact_path": path,
                            "field": field.name,
                            "metric_ref": field.metric_ref,
                            **compatibility,
                            **_binding_repair_metadata(
                                path, field.name, structure, physical_columns, records, metric_list
                            ),
                        }
                    )
    return issues


def _metric_binding_compatibility(
    artifact_path: str,
    field: str,
    metric: MetricDefinition,
    structure: dict[str, Any],
    physical_columns: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return explicit source/field/grain incompatibilities for a binding."""

    # A reusable metric can be materialized in a different derived artifact; source_artifact
    # records provenance and is not itself a presentation-table restriction.
    if metric.source_field is not None and metric.source_field != field:
        return {
            "mismatch": "source_field",
            "metric_source_field": metric.source_field,
            "required_source_field": field,
        }
    column = physical_columns.get(field, {})
    physical_grain = column.get("grain") or structure.get("grain")
    if metric.grain is not None and physical_grain is not None and metric.grain != physical_grain:
        return {
            "mismatch": "grain",
            "metric_grain": metric.grain,
            "required_grain": physical_grain,
        }
    return {}


def _binding_repair_metadata(
    artifact_path: str,
    field: str,
    structure: dict[str, Any],
    physical_columns: dict[str, dict[str, Any]],
    records: list[dict[str, Any]] | None,
    metrics: list[MetricDefinition],
) -> dict[str, Any]:
    metadata = _repair_metadata(
        artifact_path, structure, physical_columns, records=records, metrics=metrics
    )
    metadata["affected_field"] = field
    metadata["field_schema"] = physical_columns.get(field, {})
    return metadata


def _repair_metadata(
    artifact_path: str,
    structure: dict[str, Any],
    physical_columns: dict[str, dict[str, Any]],
    *,
    records: list[dict[str, Any]] | None,
    metrics: list[MetricDefinition],
) -> dict[str, Any]:
    """Build deterministic, dataset-neutral choices for report-ready repair."""

    rows = records or []
    eligible_dimensions = []
    for field, column in physical_columns.items():
        values = [row.get(field) for row in rows if not _missing(row.get(field))]
        eligible_dimensions.append(
            {
                "field": field,
                "semantic_type": column.get("semantic_type"),
                "field_type": column.get("type") or column.get("dtype"),
                "grain": column.get("grain"),
                "row_count": len(rows),
                "non_null_count": len(values),
                "distinct_count": len({str(value).strip() for value in values}),
            }
        )

    physical_measure_fields = []
    eligible_measures = []
    metric_candidates = []
    numeric_types = {"number", "integer", "float", "decimal"}
    semantic_types = {
        "integer",
        "decimal",
        "currency",
        "percentage_fraction",
        "percentage_points",
    }
    for field, column in physical_columns.items():
        field_type = str(column.get("type") or column.get("dtype") or "").lower()
        semantic_type = str(column.get("semantic_type") or "").lower()
        if field_type not in numeric_types and semantic_type not in semantic_types:
            continue
        physical_measure_fields.append(field)
        for metric in metrics:
            if metric.source_field not in {None, field}:
                continue
            metric_candidates.append(
                {
                    "field": field,
                    "metric_ref": metric.metric_id,
                    "metric_scope": metric.metric_scope,
                    "unit_family": metric.unit_family,
                    "semantic_type": metric.semantic_type,
                    "grain": metric.grain,
                    "source_artifact": metric.source_artifact,
                    "source_field": metric.source_field,
                    "presentation_eligibility": metric.metric_scope == "reusable_measure",
                }
            )
            if metric.metric_scope != "reusable_measure":
                continue
            eligible_measures.append(
                {
                    "field": field,
                    "metric_ref": metric.metric_id,
                    "metric_scope": metric.metric_scope,
                    "unit_family": metric.unit_family,
                    "semantic_type": metric.semantic_type,
                    "grain": metric.grain,
                    "source_artifact": metric.source_artifact,
                    "source_field": metric.source_field,
                    "presentation_eligibility": True,
                }
            )
    return {
        "artifact_schema": {
            "record_kind": structure.get("record_kind"),
            "row_count": structure.get("row_count", len(rows)),
            "columns": list(physical_columns.values()),
        },
        "eligible_dimensions": eligible_dimensions,
        "eligible_measures": eligible_measures,
        "metric_candidates": metric_candidates,
        "physical_measure_fields": physical_measure_fields,
        "allowed_actions": ["add_measure_binding", "remove_artifact"],
    }


def _tabular_records(path: Path) -> list[dict[str, Any]] | None:
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


def _numeric_value(value: Any) -> float | None:
    if _missing(value):
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _missing(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in {"", "null", "none", "nan"}


def _coverage_sets_compatible(row_sets: list[set[int]]) -> bool:
    populated = [rows for rows in row_sets if rows]
    if len(populated) <= 1:
        return len(populated) == len(row_sets)
    smallest = min(len(rows) for rows in populated)
    largest = max(len(rows) for rows in populated)
    if smallest / largest < 0.8:
        return False
    intersection = set.intersection(*populated)
    return len(intersection) / smallest >= 0.8


def merge_report_schema(
    structure: dict[str, Any] | None, declaration: dict[str, Any] | None
) -> dict[str, Any]:
    """Overlay persisted Analysis bindings onto freshly inspected physical schema."""

    merged = dict(structure or {})
    if not declaration:
        return merged
    fields = {
        item.get("name"): item
        for item in declaration.get("fields", [])
        if isinstance(item, dict) and item.get("name")
    }
    columns = []
    for column in merged.get("columns", []):
        item = dict(column)
        binding = fields.get(item.get("name"))
        if binding is not None:
            item.update(
                {
                    "role": binding.get("role"),
                    "metric_ref": binding.get("metric_ref"),
                    "presentation_usable": binding.get("presentation_usable", True),
                }
            )
        columns.append(item)
    merged["columns"] = columns
    merged["report_ready"] = True
    return merged
