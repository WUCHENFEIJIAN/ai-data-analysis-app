"""Generic chart-axis compatibility rules driven only by metric metadata."""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from app.services.metric_contract import (
    MetricDefinition,
    metric_display_scale,
    metric_display_unit_matches,
)

UNKNOWN_UNIT_FAMILY = "unknown"


def normalize_unit_family(value: Any) -> str:
    if value is None:
        return UNKNOWN_UNIT_FAMILY
    family = str(value).strip().lower()
    return family if family else UNKNOWN_UNIT_FAMILY


def unit_family_of_series(series: Any) -> str:
    definition = getattr(series, "metric_definition", None)
    if definition is not None:
        return normalize_unit_family(getattr(definition, "unit_family", None))
    return normalize_unit_family(getattr(series, "unit_family", None))


def can_share_ordinary_y_axis(families: Iterable[str]) -> bool:
    normalized = [normalize_unit_family(item) for item in families]
    if len(normalized) <= 1:
        return True
    unique = set(normalized)
    if UNKNOWN_UNIT_FAMILY in unique:
        return False
    return len(unique) == 1


def group_series_for_ordinary_axes(series: Iterable[Any]) -> OrderedDict[str, list[Any]]:
    groups: OrderedDict[str, list[Any]] = OrderedDict()
    unknown_index = 0
    for item in series:
        family = unit_family_of_series(item)
        if family == UNKNOWN_UNIT_FAMILY:
            key = f"unknown_{unknown_index}"
            unknown_index += 1
            groups[key] = [item]
            continue
        groups.setdefault(family, []).append(item)
    return groups


def shared_axis_display_unit(series: Iterable[Any]) -> str:
    """Return a neutral axis unit unless every series uses the same display unit.

    Unit-family compatibility decides whether series may share an ordinary axis. Display-unit
    agreement is a separate presentation concern: different authored units within one family
    remain valid on one axis, but the axis must not claim either series' unit.
    """

    units = [(getattr(item, "unit", None) or "").strip() for item in series]
    if not units:
        return ""
    first = units[0]
    return first if all(unit == first for unit in units) else ""


class ChartMetricCompatibilityError(ValueError):
    """Raised when chart series cannot share the declared axes."""


class ChartMetricCompatibilityValidator:
    """Validate single-axis and limited dual-axis charts without business rules."""

    SINGLE_AXIS_TYPES = {
        "line",
        "area",
        "bar",
        "horizontal_bar",
        "grouped_bar",
        "stacked_bar",
        "pie",
        "donut",
        "scatter",
    }

    @classmethod
    def issues(
        cls,
        chart_type: str,
        series: Iterable[Any],
        metrics: Mapping[str, MetricDefinition],
    ) -> list[str]:
        values = list(series)
        issues: list[str] = []
        by_axis: dict[str, list[tuple[Any, MetricDefinition]]] = defaultdict(list)
        for item in values:
            metric_id = str(getattr(item, "metric", ""))
            definition = metrics.get(metric_id)
            if definition is None:
                issues.append(f"unknown metric definition: {metric_id}")
                continue
            axis = str(getattr(item, "axis", "left"))
            by_axis[axis].append((item, definition))
            item_unit = (getattr(item, "unit", None) or "").strip()
            if not metric_display_unit_matches(definition, item_unit):
                issues.append(f"{metric_id}: series unit does not match metric definition")
            if float(getattr(item, "scale", 1)) != metric_display_scale(definition):
                issues.append(f"{metric_id}: series scale does not match metric definition")

        axes = set(by_axis)
        if axes - {"left", "right"} or len(axes) > 2:
            issues.append("chart may use at most left and right Y axes")
        if "right" in axes and chart_type != "combo":
            issues.append(
                "dual axes require a combo chart; preserve every series and change the "
                "chart type rather than deleting claim-required metrics"
            )
        if chart_type in cls.SINGLE_AXIS_TYPES and len(axes) > 1:
            issues.append(
                f"{chart_type} charts must use one Y axis; preserve every series and use "
                "combo when unit families differ"
            )

        axis_families: dict[str, set[str]] = {}
        for axis, items in by_axis.items():
            families = {definition.unit_family for _, definition in items}
            axis_families[axis] = families
            if len(families) > 1:
                issues.append(
                    f"{axis} axis mixes incompatible unit families: {sorted(families)}; "
                    "preserve every series and split unit families across left and right "
                    "axes in a combo chart"
                )
            formats = {str(getattr(item, "format", "number")) for item, _ in items}
            scales = {float(getattr(item, "scale", 1)) for item, _ in items}
            if len(formats) > 1 or len(scales) > 1:
                issues.append(f"{axis} axis series must share formatter and scale")

        if len(axes) == 2:
            if any(len(families) != 1 for families in axis_families.values()):
                issues.append("each dual axis must represent exactly one unit family")
            elif axis_families.get("left") == axis_families.get("right"):
                issues.append(
                    "dual axes must represent different unit families; preserve every series "
                    "and move same-family series onto one axis instead of deleting "
                    "claim-required metrics"
                )
        return list(dict.fromkeys(issues))

    @classmethod
    def validate(
        cls,
        chart_type: str,
        series: Iterable[Any],
        metrics: Mapping[str, MetricDefinition],
    ) -> None:
        issues = cls.issues(chart_type, series, metrics)
        if issues:
            raise ChartMetricCompatibilityError("; ".join(issues))
