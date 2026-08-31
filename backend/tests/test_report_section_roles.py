from types import SimpleNamespace

import pytest

from app.core.errors import ReportPipelineError
from app.services.presentation_preflight import PresentationPreflight
from app.services.report_editor_spec import (
    ReportEditorChartBlock,
    ReportEditorNarrativeBlock,
    ReportEditorSection,
    ReportEditorSpec,
)
from app.services.report_spec import (
    ChartBlock,
    ChartSpec,
    KpiGridBlock,
    NarrativeBlock,
    ProvenanceSpec,
    ReportSpec,
    SectionSpec,
    SeriesSpec,
    SourceSpec,
    TableBlock,
    TableColumnSpec,
    VisualGroupBlock,
)
from app.services.report_validator import ReportSpecValidator, _context_only_role_issue
from tests.test_report_metric_fidelity import _inputs


def _source() -> SourceSpec:
    return SourceSpec(
        id="src_summary",
        artifact_path="data/summary.csv",
        kind="csv",
        sha256="a" * 64,
        media_type="text/csv",
        usage="visual_source",
    )


def _chart() -> ChartBlock:
    return ChartBlock(
        type="chart",
        chart=ChartSpec(
            id="chart_1",
            chart_type="line",
            title="Trend",
            purpose="Compare verified values",
            source_id="src_summary",
            x_field="bucket",
            series=[
                SeriesSpec(
                    field="value",
                    label="Value",
                    metric="value",
                    format="number",
                    decimals=0,
                )
            ],
            source_caption="Source",
        ),
    )


def _spec(blocks: list[object]) -> ReportSpec:
    return ReportSpec(
        schema_version="3.0",
        locale="zh-CN",
        analysis_topic="Topic",
        title="Title",
        sources=[_source()],
        sections=[
            SectionSpec(
                id="section_1",
                title="Section",
                visual_strategy="context_only",
                blocks=blocks,
            )
        ],
        provenance=ProvenanceSpec(planner_mode="fallback"),
    )


def test_editor_context_only_with_analytical_visual_is_a_structural_issue() -> None:
    section = SimpleNamespace(section_role="context_only")
    block = ReportEditorChartBlock(
        data_ref="data/summary.csv",
        chart_type="line",
        x_field="bucket",
        series=["value"],
        title="Trend",
        purpose="Compare verified values",
    )

    issue = _context_only_role_issue(section, [block], 0, assembled=False)

    assert issue is not None
    assert issue.code == "section.role_visual_conflict"
    assert issue.repair.startswith("Change section_role to chart_led")


def test_report_editor_validator_emits_context_only_conflict_issue() -> None:
    spec = ReportEditorSpec(
        headline="Headline",
        summary="Summary",
        sections=[
            ReportEditorSection(
                title="Section",
                section_role="context_only",
                blocks=[
                    ReportEditorChartBlock(
                        data_ref="data/visual.csv",
                        chart_type="line",
                        x_field="category_a",
                        series=["value_x"],
                        title="Trend",
                        purpose="Compare verified values",
                    )
                ],
            )
        ],
    )

    result = ReportSpecValidator.validate(spec, _inputs())

    assert any(issue.code == "section.role_visual_conflict" for issue in result.issues)


def test_editor_context_only_narrative_only_is_valid() -> None:
    section = SimpleNamespace(section_role="context_only")
    block = ReportEditorNarrativeBlock(text="Context only.")

    assert _context_only_role_issue(section, [block], 0, assembled=False) is None


@pytest.mark.parametrize(
    "block,expected",
    [
        (_chart(), "chart_led"),
        (
            TableBlock(
                type="table",
                id="table_1",
                source_id="src_summary",
                title="Summary",
                purpose="Summarize verified values",
                columns=[TableColumnSpec(field="bucket", label="Bucket")],
            ),
            "table_led",
        ),
        (KpiGridBlock(type="kpi_grid", kpi_ids=["kpi_1"]), "kpi_led"),
    ],
)
def test_final_normalization_relabels_context_only_without_dropping_visuals(
    block: object, expected: str
) -> None:
    spec = _spec([block])

    normalized = PresentationPreflight(None)._apply_section_roles(spec)

    assert normalized.sections[0].visual_strategy == expected
    assert normalized.sections[0].blocks == [block]


def test_mixed_visual_context_only_prefers_chart_role() -> None:
    group = VisualGroupBlock(type="visual_group", items=[_chart(), TableBlock(
        type="table",
        id="table_1",
        source_id="src_summary",
        title="Summary",
        purpose="Summarize verified values",
        columns=[TableColumnSpec(field="bucket", label="Bucket")],
    )])

    normalized = PresentationPreflight(None)._apply_section_roles(_spec([group]))

    assert normalized.sections[0].visual_strategy == "chart_led"
    assert normalized.sections[0].blocks == [group]


def test_assembled_validator_rejects_context_only_visual_conflict_directly() -> None:
    narrative = NarrativeBlock(
        type="narrative",
        text="The chart shows the verified pattern.",
        purpose="Interpretation",
        display_role="evidence_interpretation",
        related_block_id="src_summary",
    )

    with pytest.raises(ReportPipelineError, match="context_only section"):
        ReportSpecValidator.validate_assembled(_spec([_chart(), narrative]), _inputs())


def test_assembled_validator_rejects_unresolved_context_only_visual_conflict() -> None:
    section = SimpleNamespace(id="section_1", visual_strategy="context_only")

    with pytest.raises(ReportPipelineError, match="context_only section"):
        _context_only_role_issue(section, [_chart()], 0, assembled=True, raise_on_conflict=True)
