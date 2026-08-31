"""Judge whether a ReportSpec can be rendered readably.

Allowed: axis checks, chart split, stack visual groups, fallback to an existing
summary table, omit unsafe visuals, and simple size hints.
Forbidden: re-analysis, Python, new aggregations, new metrics, story edits.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.services.chart_metric_compatibility import (
    can_share_ordinary_y_axis,
    group_series_for_ordinary_axes,
    unit_family_of_series,
)
from app.services.report_semantics import display_label_for
from app.services.report_spec import (
    ChartBlock,
    ChartSpec,
    KpiSpec,
    ReportSpec,
    SectionSpec,
    SeriesSpec,
    TableBlock,
    TableColumnSpec,
    VisualGroupBlock,
)
from app.services.report_validator import _analytical_role_for_blocks
from app.services.workspace import PathResolver


class PresentationPreflight:
    def __init__(self, resolver: PathResolver) -> None:
        self.resolver = resolver

    def normalize(self, project_id: str, spec: ReportSpec) -> ReportSpec:
        sources = {item.id: item for item in spec.sources}
        sections = [
            self._normalize_section(project_id, section, sources, spec) for section in spec.sections
        ]
        spec = spec.model_copy(update={"sections": sections})
        spec = self._apply_table_density(project_id, spec, sources)
        spec = self._apply_display_scale(project_id, spec, sources)
        return self._apply_section_roles(spec)

    def _normalize_section(
        self,
        project_id: str,
        section: SectionSpec,
        sources: dict[str, Any],
        spec: ReportSpec,
    ) -> SectionSpec:
        blocks: list[Any] = []
        for block in section.blocks:
            blocks.extend(self._normalize_block(project_id, block, section, sources, spec))
        if not blocks:
            return section
        return section.model_copy(update={"blocks": blocks})

    def _normalize_block(
        self,
        project_id: str,
        block: Any,
        section: SectionSpec,
        sources: dict[str, Any],
        spec: ReportSpec,
    ) -> list[Any]:
        if isinstance(block, ChartBlock):
            return self._normalize_chart(project_id, block, section, sources, spec)
        if isinstance(block, VisualGroupBlock):
            return [self._normalize_group(project_id, block, section, sources, spec)]
        return [block]

    def _normalize_chart(
        self,
        project_id: str,
        block: ChartBlock,
        section: SectionSpec,
        sources: dict[str, Any],
        spec: ReportSpec,
        *,
        allow_split: bool = True,
    ) -> list[Any]:
        chart = block.chart
        records = self._records(project_id, sources.get(chart.source_id), chart.records_path)
        records = records[: chart.row_limit]
        action = self._chart_action(chart, records, section, spec)
        if action == "omit":
            return []
        if action == "fallback_to_table":
            return []
        if action == "collapse_to_single_metric":
            return self._collapse_chart(block, records)
        if action == "split" and allow_split:
            return self._split_chart(project_id, block, section, sources, spec, records)
        chart = self._assign_axes(chart)
        return [block.model_copy(update={"chart": chart})]

    def _normalize_group(
        self,
        project_id: str,
        block: VisualGroupBlock,
        section: SectionSpec,
        sources: dict[str, Any],
        spec: ReportSpec,
    ) -> VisualGroupBlock:
        items: list[Any] = []
        for item in block.items:
            if isinstance(item, ChartBlock):
                items.extend(
                    self._normalize_chart(
                        project_id, item, section, sources, spec, allow_split=True
                    )
                )
            else:
                items.append(item)
        if not items:
            return block
        if len(items) > 2:
            return block.model_copy(update={"layout": "stack", "items": items[:4]})
        layout = "two-column"
        complexities = [self._complexity(project_id, item, sources) for item in items]
        if any(not item.half_width_readable for item in complexities) or (
            len(complexities) == 2 and abs(complexities[0].score - complexities[1].score) >= 2
        ):
            layout = "stack"
        return block.model_copy(update={"layout": layout, "items": items})

    def _chart_action(
        self,
        chart: ChartSpec,
        records: list[dict[str, Any]],
        section: SectionSpec,
        spec: ReportSpec,
    ) -> str:
        n = len(records)
        if len(chart.series) > 1 and not _series_coverage_compatible(chart.series, records):
            if any(_non_missing_count(records, item.field) for item in chart.series):
                return "collapse_to_single_metric"
            if _has_summary_table(section, spec, chart.source_id):
                return "fallback_to_table"
            return "omit"
        if chart.chart_type in {"pie", "donut"}:
            if len(chart.series) != 1 or n > 8:
                if _has_summary_table(section, spec, chart.source_id):
                    return "fallback_to_table"
                return "omit"
            return "render"
        families = [unit_family_of_series(item) for item in chart.series]
        if can_share_ordinary_y_axis(families):
            return "render"
        usable = [item for item in chart.series if item.presentation_usable]
        if len(usable) == 1 and can_share_ordinary_y_axis(
            [unit_family_of_series(item) for item in usable]
        ):
            return "collapse_to_single_metric"
        return "split"

    def _split_chart(
        self,
        project_id: str,
        block: ChartBlock,
        section: SectionSpec,
        sources: dict[str, Any],
        spec: ReportSpec,
        records: list[dict[str, Any]],
    ) -> list[Any]:
        groups = group_series_for_ordinary_axes(block.chart.series)
        if len(groups) <= 1:
            return [block]
        result: list[Any] = []
        for index, (family, series) in enumerate(groups.items()):
            chart_type = block.chart.chart_type
            if chart_type == "combo":
                chart_type = "line"
            title = _split_chart_title(series[0], block.chart.x_display_label)
            split = block.chart.model_copy(
                update={
                    "id": _split_id(block.chart.id, family, index),
                    "chart_type": chart_type,
                    "title": title[:240],
                    "series": [item.model_copy(update={"axis": "left"}) for item in series],
                }
            )
            child = ChartBlock(type="chart", chart=split)
            result.extend(
                self._normalize_chart(project_id, child, section, sources, spec, allow_split=False)
            )
        return result

    def _collapse_chart(
        self, block: ChartBlock, records: list[dict[str, Any]] | None = None
    ) -> list[Any]:
        usable = [item for item in block.chart.series if item.presentation_usable]
        if not usable:
            return []
        if records is not None:
            usable = sorted(
                usable,
                key=lambda item: _non_missing_count(records, item.field),
                reverse=True,
            )
        chart = block.chart.model_copy(
            update={"series": [usable[0].model_copy(update={"axis": "left"})]}
        )
        return [block.model_copy(update={"chart": chart})]

    def _assign_axes(self, chart: ChartSpec) -> ChartSpec:
        series = [item.model_copy(update={"axis": "left"}) for item in chart.series]
        return chart.model_copy(update={"series": series})

    def _complexity(self, project_id: str, block: Any, sources: dict[str, Any]) -> _Complexity:
        if isinstance(block, TableBlock):
            records = self._records(project_id, sources.get(block.source_id), block.records_path)
            n = min(len(records), block.row_limit)
            cols = len(block.columns)
            readable = cols <= 4 and n <= 8
            score = 0 if readable else (2 if cols > 6 or n > 12 else 1)
            return _Complexity(half_width_readable=readable, score=score)
        if not isinstance(block, ChartBlock):
            return _Complexity(half_width_readable=True, score=0)
        chart = block.chart
        loaded = self._records(project_id, sources.get(chart.source_id), chart.records_path)
        records = loaded[: chart.row_limit]
        labels = [str(row.get(chart.x_field, "")) for row in records]
        n = len(labels)
        max_label = max((len(item) for item in labels), default=0)
        series_n = len(chart.series)
        dual = any(item.axis == "right" for item in chart.series)
        long_series = n >= 12
        half = not (
            dual
            or chart.chart_type in {"combo", "scatter"}
            or series_n >= 3
            or n >= 12
            or (max_label >= 14 and n >= 6)
            or (chart.chart_type == "horizontal_bar" and n >= 8)
            or (chart.chart_type in {"pie", "donut"} and n >= 6)
        )
        score = 0
        if series_n >= 3 or dual:
            score += 2
        if long_series:
            score += 2
        elif n >= 8:
            score += 1
        if max_label >= 14:
            score += 1
        return _Complexity(half_width_readable=half, score=score)

    def _records(self, project_id: str, source: Any, path: list[Any]) -> list[dict[str, Any]]:
        if source is None:
            return []
        try:
            resolved = self.resolver.resolve(project_id, source.artifact_path)
        except Exception:
            return []
        if not resolved.is_file():
            return []
        try:
            value = _read_source(resolved)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, csv.Error, ValueError):
            return []
        for part in path:
            if isinstance(value, list) and isinstance(part, int) and 0 <= part < len(value):
                value = value[part]
            elif isinstance(value, dict) and isinstance(part, str) and part in value:
                value = value[part]
            else:
                return []
        if isinstance(value, dict):
            value = value.get("records", value.get("data", value))
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            return []
        return value

    def _apply_table_density(
        self, project_id: str, spec: ReportSpec, sources: dict[str, Any]
    ) -> ReportSpec:
        sections = []
        for section in spec.sections:
            blocks = [self._dense_block(project_id, block, sources) for block in section.blocks]
            sections.append(section.model_copy(update={"blocks": blocks}))
        return spec.model_copy(update={"sections": sections})

    def _dense_block(self, project_id: str, block: Any, sources: dict[str, Any]) -> Any:
        if isinstance(block, VisualGroupBlock):
            items = [self._dense_block(project_id, item, sources) for item in block.items]
            return block.model_copy(update={"items": items})
        if not isinstance(block, TableBlock):
            return block
        records = self._records(project_id, sources.get(block.source_id), block.records_path)
        n_rows = min(len(records), block.row_limit)
        internal = any(_is_internal_field(column) for column in block.columns)
        if block.usage != "summary_table":
            return block
        if len(block.columns) > 6 or n_rows > 10 or internal:
            return block.model_copy(update={"usage": "appendix"})
        return block.model_copy(update={"row_limit": min(block.row_limit, 10)})

    def _apply_display_scale(
        self, project_id: str, spec: ReportSpec, sources: dict[str, Any]
    ) -> ReportSpec:
        sections = []
        for section in spec.sections:
            blocks = [self._scale_block(project_id, block, sources) for block in section.blocks]
            sections.append(section.model_copy(update={"blocks": blocks}))
        kpis = [self._scale_kpi(kpi) for kpi in spec.kpis]
        return spec.model_copy(update={"sections": sections, "kpis": kpis})

    def _scale_block(self, project_id: str, block: Any, sources: dict[str, Any]) -> Any:
        if isinstance(block, VisualGroupBlock):
            items = [self._scale_block(project_id, item, sources) for item in block.items]
            return block.model_copy(update={"items": items})
        if isinstance(block, ChartBlock):
            records = self._records(
                project_id, sources.get(block.chart.source_id), block.chart.records_path
            )[: block.chart.row_limit]
            series = _scaled_series(block.chart.series, records)
            return block.model_copy(
                update={"chart": block.chart.model_copy(update={"series": series})}
            )
        if isinstance(block, TableBlock):
            records = self._records(project_id, sources.get(block.source_id), block.records_path)[
                : block.row_limit
            ]
            columns = _scaled_columns(block.columns, records)
            return block.model_copy(update={"columns": columns})
        return block

    @staticmethod
    def _scale_kpi(kpi: KpiSpec) -> KpiSpec:
        family = kpi.metric_definition.unit_family if kpi.metric_definition is not None else None
        if family != "currency":
            return kpi
        value = kpi.metric_definition.value if kpi.metric_definition is not None else None
        scale, unit = _choose_currency_scale(
            [value] if value is not None else [], kpi.unit, kpi.scale
        )
        return kpi.model_copy(update={"scale": scale, "unit": unit or kpi.unit})

    def _apply_section_roles(self, spec: ReportSpec) -> ReportSpec:
        sections = []
        for section in spec.sections:
            role = section.visual_strategy
            if role == "context_only":
                role = _analytical_role_for_blocks(section.blocks, assembled=True) or role
            blocks = []
            for block in section.blocks:
                if role == "table_led" and isinstance(block, VisualGroupBlock) and any(
                    isinstance(item, TableBlock) for item in block.items
                ):
                    blocks.append(block.model_copy(update={"layout": "stack"}))
                else:
                    blocks.append(block)
            sections.append(section.model_copy(update={"visual_strategy": role, "blocks": blocks}))
        return spec.model_copy(update={"sections": sections})


def _split_chart_title(series: SeriesSpec, dimension_label: str | None = None) -> str:
    """Build a safe title from the single series retained by a split chart."""

    definition = series.metric_definition
    definition_label = definition.label.strip() if definition is not None else ""
    if definition_label and definition_label not in {series.field, series.metric}:
        metric_label = definition_label
    else:
        label = series.label.strip()
        metric_label = (
            label
            if label and label not in {series.field, series.metric}
            else display_label_for(series.field)
        )
    if dimension_label and dimension_label.strip():
        return f"{dimension_label.strip()} · {metric_label}"[:240]
    return metric_label[:240]


def _is_internal_field(column: TableColumnSpec) -> bool:
    field = column.field.lower()
    return field.startswith(("_", "raw_", "debug_", "diag_"))


def _numbers(records: list[dict[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for row in records:
        try:
            values.append(float(str(row.get(field, "")).replace(",", "").rstrip("%")))
        except (TypeError, ValueError):
            continue
    return values


def _non_missing_count(records: list[dict[str, Any]], field: str) -> int:
    return sum(not _missing_value(row.get(field)) for row in records)


def _series_coverage_compatible(
    series: list[SeriesSpec], records: list[dict[str, Any]]
) -> bool:
    row_sets = [
        {
            index
            for index, row in enumerate(records)
            if not _missing_value(row.get(item.field))
        }
        for item in series
        if item.presentation_usable
    ]
    populated = [rows for rows in row_sets if rows]
    if len(populated) <= 1:
        return len(populated) == len(row_sets)
    smallest = min(len(rows) for rows in populated)
    largest = max(len(rows) for rows in populated)
    if smallest / largest < 0.8:
        return False
    return len(set.intersection(*populated)) / smallest >= 0.8


def _missing_value(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in {"", "null", "none", "nan"}


def _choose_currency_scale(
    values: list[float], unit: str | None, current_scale: int | float
) -> tuple[int, str | None]:
    if float(current_scale or 1) not in {1}:
        return int(current_scale), unit
    normalized = (unit or "").strip()
    if normalized and normalized not in {"元", "yuan", "CNY", "人民币"}:
        return 1, unit
    max_abs = max((abs(value) for value in values), default=0)
    if max_abs >= 100_000_000:
        return 100_000_000, "亿元"
    if max_abs >= 10_000:
        return 10_000, "万元"
    return 1, unit or ("元" if not normalized else unit)


def _scaled_series(series: list[SeriesSpec], records: list[dict[str, Any]]) -> list[SeriesSpec]:
    currency = [
        item
        for item in series
        if item.metric_definition is not None and item.metric_definition.unit_family == "currency"
    ]
    if not currency:
        return series
    values: list[float] = []
    for item in currency:
        values.extend(_numbers(records, item.field))
    scale, unit = _choose_currency_scale(values, currency[0].unit, currency[0].scale)
    updated: list[SeriesSpec] = []
    for item in series:
        if item in currency:
            updated.append(item.model_copy(update={"scale": scale, "unit": unit or item.unit}))
        else:
            updated.append(item)
    return updated


def _scaled_columns(
    columns: list[TableColumnSpec], records: list[dict[str, Any]]
) -> list[TableColumnSpec]:
    currency = [
        item
        for item in columns
        if (item.metric_definition is not None and item.metric_definition.unit_family == "currency")
        or item.semantic_type == "currency"
    ]
    if not currency:
        return columns
    values: list[float] = []
    for item in currency:
        values.extend(_numbers(records, item.field))
    scale, unit = _choose_currency_scale(values, currency[0].unit, currency[0].scale)
    updated: list[TableColumnSpec] = []
    for item in columns:
        if item in currency:
            updated.append(item.model_copy(update={"scale": scale, "unit": unit or item.unit}))
        else:
            updated.append(item)
    return updated


class _Complexity:
    def __init__(self, half_width_readable: bool, score: int) -> None:
        self.half_width_readable = half_width_readable
        self.score = score


def _read_source(path: Path) -> Any:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    return json.loads(path.read_text(encoding="utf-8"))


def _has_summary_table(section: SectionSpec, spec: ReportSpec, source_id: str) -> bool:
    def walk(blocks: list[Any]) -> bool:
        for block in blocks:
            if isinstance(block, TableBlock) and block.source_id == source_id:
                return True
            if isinstance(block, VisualGroupBlock) and walk(list(block.items)):
                return True
        return False

    if walk(list(section.blocks)):
        return True
    return any(walk(list(item.blocks)) for item in spec.sections)


def _split_id(chart_id: str, family: str, index: int) -> str:
    cleaned = "".join(char if char.isalnum() or char == "_" else "_" for char in family)
    suffix = cleaned[:16] or str(index + 1)
    candidate = f"{chart_id}_{suffix}"
    return candidate[:80]
