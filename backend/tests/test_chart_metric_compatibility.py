from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.chart_metric_compatibility import (
    ChartMetricCompatibilityError,
    ChartMetricCompatibilityValidator,
    can_share_ordinary_y_axis,
    group_series_for_ordinary_axes,
    normalize_unit_family,
    shared_axis_display_unit,
    unit_family_of_series,
)
from app.services.metric_contract import MetricDefinition
from app.services.report_renderer import ReportRenderer


def _metric(metric_id: str, unit_family: str, unit: str) -> MetricDefinition:
    semantic_type = {
        "count": "count",
        "percentage": "rate",
        "duration": "duration",
        "ratio": "ratio",
        "score": "score",
        "quantity": "quantity",
    }.get(unit_family, "measure")
    values = {
        "metric_id": metric_id,
        "label": metric_id,
        "value": 10,
        "aggregation": "sum",
        "semantic_type": semantic_type,
        "unit_family": unit_family,
        "unit": unit,
        "definition": "Synthetic metric for axis validation",
        "source_artifact": "data/synthetic.json",
    }
    if unit_family == "count":
        values.update(count_semantics="field_sum", is_distinct=False)
    return MetricDefinition(**values)


def _series(metric: str, unit: str, axis: str = "left") -> SimpleNamespace:
    """Build a series whose presentation fields match the Metric Contract boundary.

    Percentage metrics carry no display unit: the percent formatter owns the "%" suffix,
    so a percentage series unit must stay empty to avoid a doubled suffix.
    """

    percent = unit == "%"
    return SimpleNamespace(
        metric=metric,
        unit="" if percent else unit,
        scale=1,
        format="percent" if percent else "number",
        axis=axis,
    )


def test_same_unit_family_can_share_one_axis() -> None:
    metrics = {
        "value_a": _metric("value_a", "currency", "USD"),
        "value_b": _metric("value_b", "currency", "USD"),
    }
    ChartMetricCompatibilityValidator.validate(
        "grouped_bar",
        [_series("value_a", "USD"), _series("value_b", "USD")],
        metrics,
    )


def test_same_family_different_display_units_share_axis_with_neutral_unit() -> None:
    metrics = {
        "value_a": _metric("value_a", "count", "items_a"),
        "value_b": _metric("value_b", "count", "items_b"),
    }
    series = [_series("value_a", "items_a"), _series("value_b", "items_b")]

    ChartMetricCompatibilityValidator.validate("line", series, metrics)
    assert shared_axis_display_unit(series) == ""


def test_same_family_same_display_unit_is_used_by_shared_axis() -> None:
    series = [_series("value_a", "items"), _series("value_b", "items")]
    assert shared_axis_display_unit(series) == "items"


def test_renderer_keeps_series_units_but_neutralizes_mixed_axis_unit() -> None:
    renderer = ReportRenderer(None)
    renderer._records = lambda project_id, source, path: [
        {"bucket": "A", "value_a": 1, "value_b": 2}
    ]
    chart = SimpleNamespace(
        records_path=[],
        sort_by=None,
        sort_order="source",
        row_limit=50,
        x_field="bucket",
        chart_type="line",
        x_semantic="category",
        show_legend=True,
        show_labels=True,
        series=[
            SimpleNamespace(
                field="value_a",
                label="A",
                metric="value_a",
                format="number",
                decimals=0,
                unit="items_a",
                scale=1,
                axis="left",
                visual_type=None,
                metric_definition=None,
            ),
            SimpleNamespace(
                field="value_b",
                label="B",
                metric="value_b",
                format="number",
                decimals=0,
                unit="items_b",
                scale=1,
                axis="left",
                visual_type=None,
                metric_definition=None,
            ),
        ],
    )

    option = renderer._chart_option("project", chart, None)

    assert option["axes"][0]["unit"] == ""
    assert [item["unit"] for item in option["series"]] == ["items_a", "items_b"]


@pytest.mark.parametrize("other_family,unit", [("percentage", "%"), ("count", "items")])
def test_incompatible_unit_families_fail_on_one_axis(other_family: str, unit: str) -> None:
    metrics = {
        "value": _metric("value", "currency", "USD"),
        "other": _metric("other", other_family, unit),
    }
    with pytest.raises(ChartMetricCompatibilityError, match="incompatible unit families"):
        ChartMetricCompatibilityValidator.validate(
            "line", [_series("value", "USD"), _series("other", unit)], metrics
        )


def test_two_families_pass_only_as_explicit_combo_dual_axis() -> None:
    metrics = {
        "value": _metric("value", "currency", "USD"),
        "rate": _metric("rate", "percentage", "%"),
    }
    ChartMetricCompatibilityValidator.validate(
        "combo",
        [_series("value", "USD"), _series("rate", "%", "right")],
        metrics,
    )
    with pytest.raises(ChartMetricCompatibilityError, match="dual axes require"):
        ChartMetricCompatibilityValidator.validate(
            "line",
            [_series("value", "USD"), _series("rate", "%", "right")],
            metrics,
        )


def test_three_unit_families_cannot_hide_in_dual_axis_chart() -> None:
    metrics = {
        "value": _metric("value", "currency", "USD"),
        "rate": _metric("rate", "percentage", "%"),
        "count": _metric("count", "count", "items"),
    }
    with pytest.raises(ChartMetricCompatibilityError, match="incompatible unit families"):
        ChartMetricCompatibilityValidator.validate(
            "combo",
            [
                _series("value", "USD"),
                _series("rate", "%", "right"),
                _series("count", "items", "right"),
            ],
            metrics,
        )


def test_same_family_dual_axes_repair_preserves_series() -> None:
    metrics = {
        "value_a": _metric("value_a", "currency", "USD"),
        "value_b": _metric("value_b", "currency", "USD"),
    }
    with pytest.raises(
        ChartMetricCompatibilityError,
        match="move same-family series onto one axis instead of deleting",
    ):
        ChartMetricCompatibilityValidator.validate(
            "combo",
            [_series("value_a", "USD"), _series("value_b", "USD", "right")],
            metrics,
        )


def test_normalize_blank_unit_family_is_unknown() -> None:
    assert normalize_unit_family("") == "unknown"
    assert normalize_unit_family(None) == "unknown"
    assert normalize_unit_family("  ") == "unknown"
    assert normalize_unit_family("currency") == "currency"


def test_fixture_a_currency_and_count_cannot_share_axis() -> None:
    assert can_share_ordinary_y_axis(["currency", "count"]) is False


def test_fixture_b_currency_and_currency_can_share_axis() -> None:
    assert can_share_ordinary_y_axis(["currency", "currency"]) is True


def test_fixture_c_percentage_and_percentage_can_share_axis() -> None:
    assert can_share_ordinary_y_axis(["percentage", "percentage"]) is True


def test_fixture_d_unknown_and_unknown_cannot_auto_share_axis() -> None:
    assert can_share_ordinary_y_axis(["", ""]) is False
    assert can_share_ordinary_y_axis(["unknown", "unknown"]) is False
    series = [
        SimpleNamespace(metric_definition=None, unit_family=""),
        SimpleNamespace(metric_definition=None, unit_family=None),
    ]
    families = [unit_family_of_series(item) for item in series]
    assert families == ["unknown", "unknown"]
    groups = group_series_for_ordinary_axes(series)
    assert list(groups) == ["unknown_0", "unknown_1"]


def test_fixture_e_duration_and_count_cannot_share_axis() -> None:
    assert can_share_ordinary_y_axis(["duration", "count"]) is False


def test_production_axis_helpers_have_no_dataset_specific_predicates() -> None:
    source = Path("app/services/chart_metric_compatibility.py").read_text(encoding="utf-8")
    banned = ["成交金额", "成交客户数", "省份", "杭州", "借呗", "销售工号"]
    for token in banned:
        assert token not in source
