from pathlib import Path

from app.services.metric_contract import MetricDefinition
from app.services.presentation_preflight import PresentationPreflight
from app.services.report_editor_spec import ReportEditorSpec
from app.services.report_inputs import ReportInputCollector
from app.services.report_spec import (
    ChartBlock,
    ChartSpec,
    KpiSpec,
    ProvenanceSpec,
    ReportSpec,
    SectionSpec,
    SeriesSpec,
    SourceSpec,
    TableBlock,
    TableColumnSpec,
    VisualGroupBlock,
)
from app.services.report_validator import ReportSpecValidator
from app.services.workspace import PathResolver
from tests.test_report_editor_pipeline import _editor_spec, prepare_editor_project


def _metric(
    metric_id: str, unit_family: str, unit: str, value: float = 24475175.8
) -> MetricDefinition:
    semantic = "count" if unit_family == "count" else "measure"
    payload = {
        "metric_id": metric_id,
        "label": metric_id.replace("_", " ").title(),
        "value": value,
        "aggregation": "sum",
        "semantic_type": semantic,
        "unit_family": unit_family,
        "unit": unit,
        "definition": f"Verified {metric_id}",
        "source_artifact": "data/summary.csv",
    }
    if unit_family == "count":
        payload.update(count_semantics="field_sum", is_distinct=False, value=12)
    return MetricDefinition(**payload)


def _workspace(tmp_path: Path, rows: str) -> tuple[str, PathResolver]:
    project_id = "pj_" + "b" * 32
    root = tmp_path / project_id
    (root / "data").mkdir(parents=True)
    (root / "data" / "summary.csv").write_text(rows, encoding="utf-8")
    return project_id, PathResolver(tmp_path)


def _source() -> SourceSpec:
    return SourceSpec(
        id="src_summary",
        artifact_path="data/summary.csv",
        kind="csv",
        sha256="a" * 64,
        media_type="text/csv",
        usage="visual_source",
    )


def _spec(
    blocks: list[object], kpis: list[KpiSpec] | None = None, strategy: str = "balanced"
) -> ReportSpec:
    return ReportSpec(
        schema_version="3.0",
        locale="zh-CN",
        analysis_topic="Topic",
        title="Title",
        sources=[_source()],
        kpis=kpis or [],
        sections=[
            SectionSpec(
                id="section_1",
                title="Section",
                visual_strategy=strategy,  # type: ignore[arg-type]
                blocks=blocks,
            )
        ],
        provenance=ProvenanceSpec(planner_mode="fallback"),
    )


def test_display_scale_is_shared_inside_one_chart(tmp_path: Path) -> None:
    project_id, resolver = _workspace(
        tmp_path, "category,amount_a,amount_b\nA,24475175.8,12000000\nB,18000000,9000000\n"
    )
    metric_a = _metric("amount_a", "currency", "yuan", 24475175.8)
    metric_b = _metric("amount_b", "currency", "yuan", 12000000)
    spec = _spec(
        [
            ChartBlock(
                type="chart",
                chart=ChartSpec(
                    id="chart_1",
                    chart_type="line",
                    title="Amounts",
                    purpose="Compare",
                    source_id="src_summary",
                    x_field="category",
                    series=[
                        SeriesSpec(
                            field="amount_a",
                            label="A",
                            metric="amount_a",
                            unit="yuan",
                            metric_definition=metric_a,
                        ),
                        SeriesSpec(
                            field="amount_b",
                            label="B",
                            metric="amount_b",
                            unit="yuan",
                            metric_definition=metric_b,
                        ),
                    ],
                    source_caption="Source",
                ),
            )
        ]
    )
    updated = PresentationPreflight(resolver).normalize(project_id, spec)
    series = updated.sections[0].blocks[0].chart.series
    assert series[0].scale == series[1].scale == 10000
    assert series[0].unit == series[1].unit == "万元"
    assert metric_a.value == 24475175.8


def test_dense_summary_table_moves_to_appendix_without_dropping_columns(tmp_path: Path) -> None:
    header = ",".join(["category"] + [f"m{index}" for index in range(8)])
    row = ",".join(["A"] + ["1"] * 8)
    project_id, resolver = _workspace(tmp_path, f"{header}\n{row}\n")
    columns = [TableColumnSpec(field="category", label="Category")]
    columns.extend(TableColumnSpec(field=f"m{index}", label=f"M{index}") for index in range(8))
    spec = _spec(
        [
            TableBlock(
                type="table",
                id="table_1",
                source_id="src_summary",
                title="Wide table",
                purpose="Summary",
                usage="summary_table",
                columns=columns,
            )
        ]
    )
    updated = PresentationPreflight(resolver).normalize(project_id, spec)
    table = updated.sections[0].blocks[0]
    assert isinstance(table, TableBlock)
    assert table.usage == "appendix"
    assert len(table.columns) == 9


def test_table_led_section_stacks_visual_group(tmp_path: Path) -> None:
    project_id, resolver = _workspace(tmp_path, "category,amount\nA,10\nB,20\n")
    columns = [
        TableColumnSpec(field="category", label="Category"),
        TableColumnSpec(field="amount", label="Amount"),
    ]
    spec = _spec(
        [
            VisualGroupBlock(
                type="visual_group",
                layout="two-column",
                items=[
                    TableBlock(
                        type="table",
                        id="table_1",
                        source_id="src_summary",
                        title="Left",
                        purpose="A",
                        columns=columns,
                    ),
                    TableBlock(
                        type="table",
                        id="table_2",
                        source_id="src_summary",
                        title="Right",
                        purpose="B",
                        columns=columns,
                    ),
                ],
            )
        ],
        strategy="table_led",
    )
    updated = PresentationPreflight(resolver).normalize(project_id, spec)
    group = updated.sections[0].blocks[0]
    assert isinstance(group, VisualGroupBlock)
    assert group.layout == "stack"


def test_duplicate_claim_ids_are_flagged_but_not_rewritten(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver).collect(
            project["id"], "Analyze", "Category performance"
        )
    draft = _editor_spec()
    draft["sections"][0]["blocks"] = [
        {
            "type": "narrative",
            "text": "The measured total is 100.",
            "claim_ids": ["claim_total", "claim_total"],
            "purpose": "Repeat",
        }
    ]
    result = ReportSpecValidator.validate(ReportEditorSpec.model_validate(draft), inputs)
    assert any(issue.code == "block.duplicate_claim_ids" for issue in result.issues)
    assert result.spec.sections[0].blocks[0].text == "The measured total is 100."


def test_repeated_section_structure_is_a_signal_not_a_hard_drop(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver).collect(
            project["id"], "Analyze", "Category performance"
        )
    block = {
        "type": "narrative",
        "text": "Different topic text.",
        "purpose": "Body",
    }
    callout = {"type": "callout", "tone": "note", "text": "Note"}
    draft = _editor_spec()
    draft["sections"] = [
        {"title": f"Section {index}", "blocks": [block, callout]} for index in range(1, 4)
    ]
    result = ReportSpecValidator.validate(ReportEditorSpec.model_validate(draft), inputs)
    assert any(issue.code == "section.repeated_structure" for issue in result.issues)
    assert len(result.spec.sections) == 3
