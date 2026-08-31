"""Read-only presentation metadata derived from Metric Contract.

This module is not a second metric fact source. It never changes raw values,
formulas, aggregation, or provenance.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.metric_contract import (
    MetricDefinition,
    metric_display_scale,
    metric_display_unit,
    metric_ratio_value_basis,
)
from app.services.report_evidence import ValueFormat, ValueScale
from app.services.report_semantics import display_label_for
from app.services.report_spec import (
    ChartBlock,
    KpiSpec,
    ReportSpec,
    SeriesSpec,
    TableBlock,
    TableColumnSpec,
    VisualGroupBlock,
)


class PresentationMetadata:
    """Display-only fields copied onto KPI, chart series and table columns."""

    __slots__ = (
        "metric_id",
        "display_label",
        "display_scale",
        "display_unit",
        "decimals",
        "semantic_type",
        "unit_family",
        "canonical_unit",
        "aggregation",
        "ratio_basis",
        "ratio_value_basis",
        "format_name",
        "usable",
        "unusable_reason",
    )

    def __init__(
        self,
        *,
        metric_id: str | None,
        display_label: str,
        display_scale: float,
        display_unit: str,
        decimals: int,
        semantic_type: str,
        unit_family: str | None,
        canonical_unit: str,
        aggregation: str | None,
        ratio_basis: str | None,
        ratio_value_basis: str | None,
        format_name: str,
        usable: bool,
        unusable_reason: str | None = None,
    ) -> None:
        self.metric_id = metric_id
        self.display_label = display_label
        self.display_scale = display_scale
        self.display_unit = display_unit
        self.decimals = decimals
        self.semantic_type = semantic_type
        self.unit_family = unit_family
        self.canonical_unit = canonical_unit
        self.aggregation = aggregation
        self.ratio_basis = ratio_basis
        self.ratio_value_basis = ratio_value_basis
        self.format_name = format_name
        self.usable = usable
        self.unusable_reason = unusable_reason

    @classmethod
    def from_metric(cls, metric: MetricDefinition) -> PresentationMetadata:
        format_name, decimals = _format_and_decimals(metric)
        return cls(
            metric_id=metric.metric_id,
            display_label=metric.label,
            display_scale=_as_value_scale(_display_scale(metric)),
            display_unit=metric_display_unit(metric),
            decimals=decimals,
            semantic_type=metric.semantic_type,
            unit_family=metric.unit_family,
            canonical_unit=metric.unit or "",
            aggregation=metric.aggregation,
            ratio_basis=metric.ratio_basis,
            ratio_value_basis=metric_ratio_value_basis(metric),
            format_name=format_name,
            usable=True,
        )

    @classmethod
    def unusable(cls, field: str, reason: str) -> PresentationMetadata:
        return cls(
            metric_id=None,
            display_label=display_label_for(field),
            display_scale=1,
            display_unit="",
            decimals=0,
            semantic_type="unknown",
            unit_family=None,
            canonical_unit="",
            aggregation=None,
            ratio_basis=None,
            ratio_value_basis=None,
            format_name="number",
            usable=False,
            unusable_reason=reason,
        )


class PresentationMetadataResolver:
    """Map fields to existing metrics and copy display metadata onto a spec."""

    @classmethod
    def metric_for_field(
        cls,
        field: str,
        metrics: Mapping[str, MetricDefinition],
        *,
        declared_metric: str | None = None,
    ) -> MetricDefinition | None:
        if declared_metric and declared_metric in metrics:
            return metrics[declared_metric]
        if field in metrics:
            return metrics[field]
        return None

    @classmethod
    def resolve_measure(
        cls,
        field: str,
        metrics: Mapping[str, MetricDefinition],
        *,
        declared_metric: str | None = None,
        explicit_definition: MetricDefinition | None = None,
    ) -> PresentationMetadata:
        if explicit_definition is not None:
            return PresentationMetadata.from_metric(explicit_definition)
        metric = cls.metric_for_field(field, metrics, declared_metric=declared_metric)
        if metric is None:
            return PresentationMetadata.unusable(field, "no metric contract for this field")
        return PresentationMetadata.from_metric(metric)

    @classmethod
    def apply(
        cls,
        spec: ReportSpec,
        metrics: Mapping[str, MetricDefinition] | None = None,
    ) -> ReportSpec:
        registry: dict[str, MetricDefinition] = dict(metrics or {})
        for kpi in spec.kpis:
            if kpi.metric_definition is not None:
                registry.setdefault(kpi.metric_definition.metric_id, kpi.metric_definition)
        for section in spec.sections:
            for block in section.blocks:
                cls._collect_metrics(block, registry)
        kpis = [cls._apply_kpi(kpi, registry) for kpi in spec.kpis]
        sections = [
            section.model_copy(
                update={"blocks": [cls._apply_block(block, registry) for block in section.blocks]}
            )
            for section in spec.sections
        ]
        return spec.model_copy(update={"kpis": kpis, "sections": sections})

    @classmethod
    def _collect_metrics(cls, block: Any, registry: dict[str, MetricDefinition]) -> None:
        if isinstance(block, ChartBlock):
            for series in block.chart.series:
                if series.metric_definition is not None:
                    registry.setdefault(
                        series.metric_definition.metric_id, series.metric_definition
                    )
        elif isinstance(block, TableBlock):
            for column in block.columns:
                if column.metric_definition is not None:
                    registry.setdefault(
                        column.metric_definition.metric_id, column.metric_definition
                    )
        elif isinstance(block, VisualGroupBlock):
            for item in block.items:
                cls._collect_metrics(item, registry)

    @classmethod
    def _apply_block(cls, block: Any, metrics: Mapping[str, MetricDefinition]) -> Any:
        if isinstance(block, ChartBlock):
            series = [cls._apply_series(item, metrics) for item in block.chart.series]
            return block.model_copy(
                update={"chart": block.chart.model_copy(update={"series": series})}
            )
        if isinstance(block, TableBlock):
            columns = [cls._apply_column(item, metrics) for item in block.columns]
            return block.model_copy(update={"columns": columns})
        if isinstance(block, VisualGroupBlock):
            return block.model_copy(
                update={"items": [cls._apply_block(item, metrics) for item in block.items]}
            )
        return block

    @classmethod
    def _apply_kpi(cls, kpi: KpiSpec, metrics: Mapping[str, MetricDefinition]) -> KpiSpec:
        meta = cls.resolve_measure(
            kpi.metric,
            metrics,
            declared_metric=kpi.metric,
            explicit_definition=kpi.metric_definition,
        )
        if not meta.usable:
            return kpi
        return kpi.model_copy(
            update={
                "display_label": kpi.display_label or meta.display_label,
                "format": _as_value_format(meta.format_name),
                "decimals": meta.decimals,
                "unit": meta.display_unit or None,
                "scale": _as_value_scale(meta.display_scale),
                "metric_definition": kpi.metric_definition or metrics.get(meta.metric_id or ""),
            }
        )

    @classmethod
    def _apply_series(
        cls, series: SeriesSpec, metrics: Mapping[str, MetricDefinition]
    ) -> SeriesSpec:
        meta = cls.resolve_measure(
            series.field,
            metrics,
            declared_metric=series.metric,
            explicit_definition=series.metric_definition,
        )
        if not meta.usable:
            return series.model_copy(
                update={
                    "presentation_usable": False,
                    "format": "number",
                    "decimals": 0,
                    "unit": None,
                    "scale": 1,
                }
            )
        return series.model_copy(
            update={
                "presentation_usable": True,
                "format": _as_value_format(meta.format_name),
                "decimals": meta.decimals,
                "unit": meta.display_unit or None,
                "scale": _as_value_scale(meta.display_scale),
                "metric": meta.metric_id or series.metric,
                "metric_definition": series.metric_definition or metrics.get(meta.metric_id or ""),
            }
        )

    @classmethod
    def _apply_column(
        cls, column: TableColumnSpec, metrics: Mapping[str, MetricDefinition]
    ) -> TableColumnSpec:
        meta = cls.resolve_measure(
            column.field,
            metrics,
            declared_metric=column.metric,
            explicit_definition=column.metric_definition,
        )
        if not meta.usable:
            return column.model_copy(update={"presentation_usable": False})
        semantic = _table_semantic(meta, column.semantic_type)
        return column.model_copy(
            update={
                "presentation_usable": True,
                "format": _column_format(meta.format_name, semantic),
                "decimals": meta.decimals,
                "unit": meta.display_unit or None,
                "scale": _as_value_scale(meta.display_scale),
                "metric": meta.metric_id,
                "metric_definition": column.metric_definition or metrics.get(meta.metric_id or ""),
                "semantic_type": semantic,
            }
        )


def _format_and_decimals(metric: MetricDefinition) -> tuple[str, int]:
    if metric.unit_family == "currency":
        return "currency", 2
    if metric.unit_family == "percentage":
        return "percent", 2
    if metric.unit_family == "count" or metric.semantic_type == "count":
        return "integer", 0
    return "number", 2


def _display_scale(metric: MetricDefinition) -> float:
    return metric_display_scale(metric)

def _table_semantic(meta: PresentationMetadata, declared: str) -> str:
    if meta.unit_family == "percentage":
        if meta.ratio_value_basis == "fraction":
            return "percentage_fraction"
        if meta.ratio_value_basis == "percent":
            return "percentage_points"
        if declared in {"percentage_fraction", "percentage_points"}:
            return declared
        return "percentage_points"
    if meta.unit_family == "currency":
        return "currency"
    if meta.unit_family == "count" or meta.semantic_type == "count":
        return "integer"
    if declared != "text":
        return declared
    return "decimal"


def _column_format(format_name: str, semantic: str) -> str:
    if semantic in {"text", "identifier", "date", "datetime"}:
        return "text"
    if format_name in {"currency", "percent", "integer", "number"}:
        return format_name
    return "number"


def _as_value_format(format_name: str) -> ValueFormat:
    if format_name in {"number", "integer", "currency", "percent"}:
        return format_name  # type: ignore[return-value]
    return "number"


def _as_value_scale(value: float | int) -> ValueScale:
    numeric = float(value)
    return numeric if numeric > 0 else 1
