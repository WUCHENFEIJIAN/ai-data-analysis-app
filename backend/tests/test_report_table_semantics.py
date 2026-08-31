"""Generic table semantics: identifiers, percentages, display labels, preview cap."""

from types import SimpleNamespace

from app.services.artifact_schema import ArtifactSchemaInspector
from app.services.report_renderer import ReportRenderer
from app.services.report_semantics import (
    classify_column_values,
    display_label_for,
    format_table_value,
)
from app.services.report_spec import TableColumnSpec


def test_identifier_is_not_thousands_or_decimal() -> None:
    assert classify_column_values(["1000003926", "1000003927"]) == "identifier"
    assert format_table_value("1000003926.0", "identifier") == "1000003926"
    assert format_table_value(1000003926, "identifier") == "1000003926"
    assert "," not in format_table_value(1000003926, "identifier")
    assert "e" not in format_table_value(1000003926, "identifier").lower()


def test_percentage_fraction_and_points_are_explicit() -> None:
    assert format_table_value(0.4665, "percentage_fraction") == "46.65%"
    assert format_table_value(46.65, "percentage_points") == "46.65%"
    assert classify_column_values(["0.4665", "0.2", "0.0"]) == "percentage_fraction"


def test_display_label_humanizes_internal_fields() -> None:
    assert display_label_for("value_per_active_day") == "Value Per Active Day"
    column = TableColumnSpec(
        field="value_per_active_day",
        label="日均成交金额",
        semantic_type="decimal",
        format="number",
        decimals=2,
    )
    assert column.field == "value_per_active_day"
    assert column.label == "日均成交金额"


def test_renderer_uses_display_label_and_identifier_format() -> None:
    column = TableColumnSpec(
        field="entity_id",
        label="实体编号",
        semantic_type="identifier",
        format="text",
    )
    renderer = SimpleNamespace()
    text = ReportRenderer._table_value(renderer, "1000003926.0", column)
    assert text == "1000003926"
    percent = ReportRenderer._table_value(
        renderer,
        "0.4665",
        TableColumnSpec(
            field="share",
            label="份额",
            semantic_type="percentage_fraction",
            format="percent",
            decimals=2,
        ),
    )
    assert percent == "46.65%"


def test_schema_inspector_adds_semantic_metadata(tmp_path) -> None:
    path = tmp_path / "sample.csv"
    path.write_text(
        "entity_id,share,label\n1000003926,0.4665,alpha\n1000003927,0.2,beta\n",
        encoding="utf-8",
    )
    structure = ArtifactSchemaInspector().inspect(path)
    by_name = {item["name"]: item for item in structure["columns"]}
    assert by_name["entity_id"]["semantic_type"] == "identifier"
    assert by_name["share"]["semantic_type"] == "percentage_fraction"
    assert by_name["entity_id"]["display_label"] == "Entity Id"
    assert by_name["label"]["display_label"] == "label"


def test_production_table_code_has_no_sales_field_special_cases() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "services"
    forbidden = ["成交日期", "成交金额", "销售工号", "借呗", "花呗", "业务组"]
    for path in [
        root / "report_semantics.py",
        root / "report_renderer.py",
        root / "report_editor_assembler.py",
        root / "artifact_schema.py",
        root / "presentation_metadata.py",
        root / "presentation_preflight.py",
        root / "report_validator.py",
        root / "report_spec.py",
    ]:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} contains {token}"
