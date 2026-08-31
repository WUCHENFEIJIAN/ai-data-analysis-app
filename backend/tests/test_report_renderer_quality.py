import json
import re
import subprocess

import pytest

from app.core.errors import AppError
from app.services.report_renderer import (
    EMBEDDED_ECHARTS_RUNTIME,
    REPORT_DESIGN_TOKENS,
    ReportRenderer,
)


def _render_runtime(option: dict) -> str:
    script = (
        "var window={};var output='';"
        "var document={getElementById:function(){return {set innerHTML(v){output=v;}};}};"
        + EMBEDDED_ECHARTS_RUNTIME
        + "window.echarts.init(document.getElementById('chart')).setOption("
        + json.dumps(option)
        + ");console.log(output);"
    )
    result = subprocess.run(
        ["node", "-"],
        input=script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_missing_chart_point_is_not_rendered_as_numeric_zero() -> None:
    svg = _render_runtime(
        {
            "chartType": "line",
            "labels": ["Missing", "Zero", "Positive"],
            "showLegend": False,
            "showLabels": False,
            "color": ["#123456"],
            "surface": "#fff",
            "title": {"text": "Missing versus zero"},
            "series": [
                {
                    "name": "Metric",
                    "type": "line",
                    "data": [None, 0, 5],
                    "format": "number",
                    "decimals": 2,
                    "scale": 1,
                    "axis": "left",
                }
            ],
        }
    )

    assert len(re.findall(r'class="data-point"', svg)) == 2
    assert "Metric: 0.00" in svg


def test_bar_with_complete_labels_hides_numeric_axis_and_legend() -> None:
    svg = _render_runtime(
        {
            "chartType": "bar",
            "labels": ["A", "B"],
            "showLegend": True,
            "showLabels": True,
            "color": ["#123456"],
            "surface": "#fff",
            "title": {"text": "Values"},
            "series": [
                {
                    "name": "Value",
                    "type": "bar",
                    "data": [100, 80],
                    "format": "number",
                    "decimals": 0,
                    "unit": "",
                    "scale": 1,
                    "axis": "left",
                }
            ],
        }
    )
    assert svg.count(">100<") == 1
    assert ">0<" not in svg
    assert "Value</text>" not in svg


def test_line_has_nice_ticks_and_top_padding() -> None:
    svg = _render_runtime(
        {
            "chartType": "line",
            "labels": ["Apr", "May", "Jun"],
            "showLegend": False,
            "showLabels": False,
            "color": ["#123456"],
            "surface": "#fff",
            "title": {"text": "Trend"},
            "series": [
                {
                    "name": "Revenue",
                    "type": "line",
                    "data": [16154800, 12000000, 9000000],
                    "format": "currency",
                    "decimals": 0,
                    "unit": "万元",
                    "scale": 10000,
                    "axis": "left",
                }
            ],
        }
    )
    # Six tick labels (0 plus five major intervals) are emitted on the left.
    assert svg.count("万元</text>") >= 5
    assert ">0万元</text>" in svg
    assert "1615万元</text>" not in svg


@pytest.mark.parametrize("maximum", [0.12, 12, 137, 2400, 32_000_000, 72, 500])
def test_nice_scale_is_generic_across_magnitudes(maximum: float) -> None:
    svg = _render_runtime(
        {
            "chartType": "line",
            "labels": ["A", "B"],
            "showLegend": False,
            "showLabels": False,
            "color": ["#123456"],
            "surface": "#fff",
            "title": {"text": "Scale"},
            "series": [
                {
                    "name": "Value",
                    "type": "line",
                    "data": [maximum * 0.6, maximum],
                    "format": "number",
                    "decimals": 2,
                    "unit": "u",
                    "scale": 1,
                    "axis": "left",
                }
            ],
        }
    )
    ticks = [float(value) for value in re.findall(r'data-raw-value="([^"]+)"', svg)]
    assert 5 <= len(ticks) <= 7
    assert ticks == sorted(set(ticks))
    assert ticks[-1] > maximum
    assert ticks[-1] / maximum <= 1.35


def test_scaled_axis_ticks_are_not_all_zero() -> None:
    svg = _render_runtime(
        {
            "chartType": "line",
            "labels": ["Apr", "May", "Jun"],
            "showLegend": False,
            "showLabels": False,
            "color": ["#123456"],
            "surface": "#fff",
            "title": {"text": "Scaled trend"},
            "series": [
                {
                    "name": "Value",
                    "type": "line",
                    "data": [16_154_800, 12_000_000, 9_000_000],
                    "format": "currency",
                    "decimals": 2,
                    "unit": "万元",
                    "scale": 10_000,
                    "axis": "left",
                }
            ],
        }
    )
    labels = re.findall(r'<text class="axis-tick"[^>]*>([^<]+)</text>', svg)
    assert len(set(labels)) > 1
    assert labels != ["0.00万元"] * len(labels)
    assert 'data-scale="10000"' in svg


def test_runtime_has_one_cartesian_implementation() -> None:
    assert EMBEDDED_ECHARTS_RUNTIME.count("function cartesian(") == 1
    assert "cartesianNice" not in EMBEDDED_ECHARTS_RUNTIME


def test_horizontal_bar_has_value_labels_without_numeric_axis() -> None:
    svg = _render_runtime(
        {
            "chartType": "horizontal_bar",
            "labels": ["A", "B"],
            "showLegend": False,
            "showLabels": True,
            "color": ["#123456"],
            "surface": "#fff",
            "title": {"text": "Rank"},
            "series": [
                {
                    "name": "Value",
                    "type": "bar",
                    "data": [121, 118],
                    "format": "number",
                    "decimals": 0,
                    "unit": "万",
                    "scale": 1,
                    "axis": "left",
                }
            ],
        }
    )
    assert ">A</text>" in svg and ">B</text>" in svg
    assert ">121万</text>" in svg and ">118万</text>" in svg
    assert ">0万</text>" not in svg


@pytest.mark.parametrize(
    "document",
    [
        "<!doctype html><html><head><style>a{color:red}}</style></head><body></body></html>",
        "<!doctype html><html><body><div></body></html>",
    ],
)
def test_html_validator_rejects_malformed_css_or_tags(document: str) -> None:
    with pytest.raises(AppError):
        ReportRenderer.validate_html(document)


def _line_option(labels, values, show_labels=True, x_semantic="date"):
    return {
        "chartType": "line",
        "labels": labels,
        "xSemantic": x_semantic,
        "showLegend": False,
        "showLabels": show_labels,
        "color": ["#123456"],
        "surface": "#fff",
        "title": {"text": "Trend"},
        "series": [
            {
                "name": "Value",
                "type": "line",
                "data": values,
                "format": "number",
                "decimals": 0,
                "unit": "",
                "scale": 1,
                "axis": "left",
            }
        ],
    }


def test_long_time_series_keeps_all_points_but_thins_ticks() -> None:
    labels = [f"2020-04-{day:02d}" for day in range(1, 31)] + [
        f"2020-05-{day:02d}" for day in range(1, 31)
    ]
    svg = _render_runtime(_line_option(labels, list(range(1, 61))))
    points = re.findall(r'class="data-point"', svg)
    ticks = re.findall(r'class="category-label"', svg)
    raw = re.findall(r'data-raw-label="([^"]+)"', svg)
    assert len(points) == 60
    assert 2 <= len(ticks) < 15
    assert raw[0] == labels[0]
    assert raw[-1] == labels[-1]
    visible = re.findall(r'<text class="category-label"[^>]*>([^<]+)</text>', svg)
    assert all("2020-04-" not in label for label in visible)


def test_short_time_series_keeps_all_ticks() -> None:
    labels = ["2020-04-01", "2020-04-02", "2020-04-03", "2020-04-04", "2020-04-05"]
    svg = _render_runtime(_line_option(labels, [10, 20, 30, 40, 50]))
    ticks = re.findall(r'class="category-label"', svg)
    raw = re.findall(r'data-raw-label="([^"]+)"', svg)
    assert len(ticks) == 5
    assert raw[0] == "2020-04-01"
    assert raw[-1] == "2020-04-05"
    visible = re.findall(r'<text class="category-label"[^>]*>([^<]+)</text>', svg)
    assert visible[0] == "4/1"
    assert visible[-1] == "4/5"


def test_dense_line_hides_value_labels_even_when_requested() -> None:
    labels = [f"2020-04-{day:02d}" for day in range(1, 31)] + [
        f"2020-05-{day:02d}" for day in range(1, 21)
    ]
    svg = _render_runtime(_line_option(labels, list(range(1, 51)), show_labels=True))
    assert len(re.findall(r'class="data-point"', svg)) == 50
    assert len(re.findall(r'class="value-label"', svg)) == 0


def test_sparse_line_can_show_value_labels() -> None:
    labels = ["2020-04-01", "2020-04-02", "2020-04-03", "2020-04-04", "2020-04-05"]
    svg = _render_runtime(_line_option(labels, [11, 12, 13, 14, 15], show_labels=True))
    assert len(re.findall(r'class="value-label"', svg)) == 5


def test_cross_year_ticks_use_year_month() -> None:
    labels = ["2020-12-01", "2020-12-15", "2021-01-01", "2021-01-15", "2021-02-01"]
    svg = _render_runtime(_line_option(labels, [1, 2, 3, 4, 5]))
    visible = re.findall(r'<text class="category-label"[^>]*>([^<]+)</text>', svg)
    assert visible[0] == "2020/12"
    assert visible[-1] == "2021/2"


def test_x_semantic_uses_values_not_field_names() -> None:
    from app.services.report_renderer import _x_semantic

    assert _x_semantic(["2020-04-01", "2020-04-02"]) == "date"
    assert _x_semantic(["A", "B", "C"]) == "category"
    assert _x_semantic(["2021-01-01 08:00", "2021-01-02 09:00"]) == "datetime"


def test_horizontal_bar_height_grows_with_categories() -> None:
    short = _render_runtime(
        {
            "chartType": "horizontal_bar",
            "labels": ["A", "B"],
            "showLegend": False,
            "showLabels": True,
            "color": ["#123456"],
            "surface": "#fff",
            "title": {"text": "Short"},
            "series": [
                {
                    "name": "Value",
                    "type": "bar",
                    "data": [10, 20],
                    "format": "number",
                    "decimals": 0,
                    "unit": "",
                    "scale": 1,
                    "axis": "left",
                }
            ],
        }
    )
    tall = _render_runtime(
        {
            "chartType": "horizontal_bar",
            "labels": [f"Item {index}" for index in range(12)],
            "showLegend": False,
            "showLabels": False,
            "color": ["#123456"],
            "surface": "#fff",
            "title": {"text": "Tall"},
            "series": [
                {
                    "name": "Value",
                    "type": "bar",
                    "data": list(range(12)),
                    "format": "number",
                    "decimals": 0,
                    "unit": "",
                    "scale": 1,
                    "axis": "left",
                }
            ],
        }
    )
    short_h = int(re.search(r'viewBox="0 0 \d+ (\d+)"', short).group(1))
    tall_h = int(re.search(r'viewBox="0 0 \d+ (\d+)"', tall).group(1))
    assert tall_h > short_h
    assert "min-height:300px" not in ReportRenderer._css(REPORT_DESIGN_TOKENS["editorial"])


def test_x_semantic_supports_year_and_month_values() -> None:
    from app.services.report_renderer import _x_semantic

    assert _x_semantic(["2022", "2023", "2024"]) == "date"
    assert _x_semantic(["2024-01", "2024-03", "2024-04"]) == "date"


def test_temporal_points_use_real_time_spacing() -> None:
    labels = ["2024-01", "2024-03", "2024-04"]
    svg = _render_runtime(_line_option(labels, [1, 2, 3], x_semantic="date"))
    xs = [float(value) for value in re.findall(r'class="data-point"[^>]* cx="([0-9.]+)"', svg)]

    assert len(xs) == 3
    assert xs[1] - xs[0] > xs[2] - xs[1]
