"""Report width system and section layout contracts."""

from app.services.report_renderer import REPORT_DESIGN_TOKENS, ReportRenderer


def _css() -> str:
    return ReportRenderer._css(REPORT_DESIGN_TOKENS["editorial"])


def test_report_width_tokens_are_unified() -> None:
    css = _css()
    assert "--content-width:" in css
    assert "--reading-width:" not in css
    assert "--visual-width:" not in css
    assert "--report-frame-width:" not in css
    assert "--space-section:" in css
    assert "--space-block:" in css
    assert "--space-text:" in css
    assert "--space-visual:" in css
    assert "max-width:900px" not in css
    assert "max-width:860px" not in css
    assert "min-height:300px" not in css


def test_reading_and_visual_classes_share_token_widths() -> None:
    css = _css()
    assert ".reading-measure{width:100%}" in css
    assert ".wide-visual{width:100%}" in css
    assert ".summary{" in css
    assert ".editorial-narrative p{" in css
    assert ".callout{" in css
    assert ".chart-card,.table-card,.artifact-image{" in css


def test_section_two_column_no_longer_grids_all_blocks() -> None:
    css = _css()
    assert ".layout-two-column .blocks{display:grid" not in css
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in css
    assert ".visual-group-two-column{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))" in css
    assert ".blocks{display:flex;flex-direction:column" in css
    assert ".visual-group-stack{" in css
