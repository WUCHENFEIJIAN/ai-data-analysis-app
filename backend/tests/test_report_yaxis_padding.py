import re

from tests.test_report_renderer_quality import _render_runtime

SVG_WIDTH = 960
CHAR_WIDTH = 7.4
TICK_INSET = 9


def _estimate_width(text: str) -> float:
    return max(10, len(text) * CHAR_WIDTH)


def _option(values: list[float], **series_overrides) -> dict:
    series = {
        "name": "metric_x",
        "type": "line",
        "data": values,
        "format": "number",
        "decimals": 0,
        "unit": "",
        "scale": 1,
        "axis": "left",
    }
    series.update(series_overrides)
    return {
        "chartType": "line",
        "labels": ["A", "B", "C"][: len(values)],
        "showLegend": False,
        "showLabels": False,
        "color": ["#123456"],
        "surface": "#fff",
        "title": {"text": "metric_x"},
        "series": [series],
    }


def _left_ticks(svg: str) -> list[tuple[float, str]]:
    ticks = []
    for match in re.finditer(
        r'<text class="axis-tick"[^>]*data-axis="left"[^>]*x="([^"]+)"[^>]*>([^<]+)</text>',
        svg,
    ):
        ticks.append((float(match.group(1)), match.group(2)))
    if ticks:
        return ticks
    for match in re.finditer(
        r'<text class="axis-tick"[^>]*x="([^"]+)"[^>]*>([^<]+)</text>',
        svg,
    ):
        ticks.append((float(match.group(1)), match.group(2)))
    return ticks


def _assert_left_ticks_fit(svg: str) -> list[tuple[float, str]]:
    ticks = _left_ticks(svg)
    assert ticks
    for x, label in ticks:
        leftmost = x - _estimate_width(label)
        assert leftmost >= 0, (label, x, leftmost)
        assert x + 1 <= SVG_WIDTH
    return ticks


def test_case_a_short_ticks_do_not_over_pad() -> None:
    svg = _render_runtime(_option([0, 50, 100]))
    ticks = _assert_left_ticks_fit(svg)
    labels = {label for _, label in ticks}
    assert "0" in labels or "0.00" in labels or any(label.startswith("0") for label in labels)
    assert min(x for x, _ in ticks) <= 40


def test_case_b_million_tick_is_fully_visible() -> None:
    svg = _render_runtime(_option([400000, 1000000], format="number", decimals=0, unit=""))
    ticks = _assert_left_ticks_fit(svg)
    labels = [label for _, label in ticks]
    assert any("1,000,000" in label or "1000000" in label or "1,000" in label for label in labels)


def test_case_c_decimal_with_unit_is_fully_visible() -> None:
    svg = _render_runtime(
        _option([500, 1234.56], format="number", decimals=2, unit=" unit")
    )
    ticks = _assert_left_ticks_fit(svg)
    labels = [label for _, label in ticks]
    assert any("unit" in label for label in labels)
    assert any("." in label for label in labels)


def test_case_d_percent_tick_is_fully_visible() -> None:
    svg = _render_runtime(
        _option([40, 100], format="percent", decimals=2, unit="%")
    )
    ticks = _assert_left_ticks_fit(svg)
    labels = [label for _, label in ticks]
    assert any("%" in label for label in labels)


def test_case_e_long_unit_uses_padding_cap_or_stays_in_viewbox() -> None:
    unit = " generic_unit_label_that_is_very_long"
    svg = _render_runtime(_option([40, 100], format="number", decimals=0, unit=unit))
    ticks = _left_ticks(svg)
    assert ticks
    max_width = max(_estimate_width(label) for _, label in ticks)
    xs = [x for x, _ in ticks]
    if max_width + 12 > 220:
        assert min(xs) >= 220 - TICK_INSET - 1
    else:
        _assert_left_ticks_fit(svg)


def test_runtime_no_longer_uses_fixed_72_or_96_left_padding() -> None:
    from app.services.report_renderer import EMBEDDED_ECHARTS_RUNTIME

    assert "longest>10?96:72" not in EMBEDDED_ECHARTS_RUNTIME
    assert "yAxisPadding" in EMBEDDED_ECHARTS_RUNTIME
    assert "estimateTextWidth" in EMBEDDED_ECHARTS_RUNTIME
