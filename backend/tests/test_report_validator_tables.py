from types import SimpleNamespace

import pytest

from app.services.report_spec import TableBlock, TableColumnSpec, VisualGroupBlock
from app.services.report_validator import ReportSpecValidator


@pytest.mark.parametrize("grouped", [False, True])
def test_assembled_table_visual_groups_use_source_id(grouped: bool) -> None:
    table = TableBlock(
        type="table",
        id="table_1",
        source_id="artifact_visual",
        title="Metric table",
        purpose="Show verified metrics",
        columns=[TableColumnSpec(field="value", label="Value")],
    )
    blocks = [VisualGroupBlock(type="visual_group", items=[table])] if grouped else [table]
    section = SimpleNamespace(visual_strategy="table_led")

    assert ReportSpecValidator._interpretation_groups(section, blocks, assembled=True) == [
        {"artifact_visual"}
    ]
