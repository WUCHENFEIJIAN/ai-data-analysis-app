from pathlib import Path

from app.services.metric_contract import MetricDefinition
from app.services.presentation_preflight import PresentationPreflight
from app.services.report_spec import (
    ChartBlock,
    ChartSpec,
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
from app.services.workspace import PathResolver


def _metric(metric_id: str, unit_family: str, unit: str) -> MetricDefinition:
    if unit_family == "count":
        semantic = "count"
    elif unit_family == "percentage":
        semantic = "rate"
    elif unit_family == "duration":
        semantic = "duration"
    else:
        semantic = "measure"
    payload = {
        "metric_id": metric_id,
        "label": metric_id.replace("_", " ").title(),
        "value": 10,
        "aggregation": "sum",
        "semantic_type": semantic,
        "unit_family": unit_family,
        "unit": unit,
        "definition": f"Verified {metric_id}",
        "source_artifact": "data/summary.csv",
    }
    if unit_family == "count":
        payload.update(count_semantics="field_sum", is_distinct=False)
    return MetricDefinition(**payload)


def _workspace(tmp_path: Path, rows: str) -> tuple[str, PathResolver]:
    project_id = "pj_" + "a" * 32
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


def _chart(
    series: list[SeriesSpec],
    chart_type: str = "line",
    chart_id: str = "chart_1",
) -> ChartBlock:
    return ChartBlock(
        type="chart",
        chart=ChartSpec(
            id=chart_id,
            chart_type=chart_type,
            title="Mixed series",
            purpose="Compare",
            source_id="src_summary",
            x_field="category",
            series=series,
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
        sections=[SectionSpec(id="section_1", title="Section", blocks=blocks)],
        provenance=ProvenanceSpec(planner_mode="fallback"),
    )


def test_amount_and_count_are_split_not_reaggregated(tmp_path: Path) -> None:
    project_id, resolver = _workspace(tmp_path, "category,amount,orders\nA,10,2\nB,20,3\n")
    amount = _metric("amount", "currency", "yuan")
    orders = _metric("orders", "count", "items")
    spec = _spec(
        [
            _chart(
                [
                    SeriesSpec(
                        field="amount",
                        label="Amount",
                        metric="amount",
                        metric_definition=amount,
                    ),
                    SeriesSpec(
                        field="orders",
                        label="Orders",
                        metric="orders",
                        metric_definition=orders,
                    ),
                ]
            )
        ]
    )
    updated = PresentationPreflight(resolver).normalize(project_id, spec)
    charts = [block.chart for block in updated.sections[0].blocks if isinstance(block, ChartBlock)]
    assert len(charts) == 2
    families = [{item.metric_definition.unit_family for item in chart.series} for chart in charts]
    assert {"currency"} in families
    assert {"count"} in families


def test_unsuitable_chart_falls_back_to_existing_table(tmp_path: Path) -> None:
    project_id, resolver = _workspace(
        tmp_path, "category,amount\n" + "\n".join(f"C{index},{index}" for index in range(12))
    )
    amount = _metric("amount", "currency", "yuan")
    spec = _spec(
        [
            _chart(
                [
                    SeriesSpec(
                        field="amount",
                        label="Amount",
                        metric="amount",
                        metric_definition=amount,
                    )
                ],
                chart_type="pie",
            ),
            TableBlock(
                type="table",
                id="table_1",
                source_id="src_summary",
                title="Summary",
                purpose="Existing table",
                columns=[
                    TableColumnSpec(field="category", label="Category"),
                    TableColumnSpec(field="amount", label="Amount", metric="amount"),
                ],
            ),
        ]
    )
    updated = PresentationPreflight(resolver).normalize(project_id, spec)
    types = [block.type for block in updated.sections[0].blocks]
    assert "chart" not in types
    assert "table" in types


def test_unsuitable_chart_is_omitted_without_inventing_a_table(tmp_path: Path) -> None:
    project_id, resolver = _workspace(
        tmp_path, "category,amount\n" + "\n".join(f"C{index},{index}" for index in range(12))
    )
    amount = _metric("amount", "currency", "yuan")
    spec = _spec(
        [
            NarrativeBlock(
                type="narrative",
                text="The section still has context after the chart is omitted.",
                purpose="Keep the section",
            ),
            _chart(
                [
                    SeriesSpec(
                        field="amount",
                        label="Amount",
                        metric="amount",
                        metric_definition=amount,
                    )
                ],
                chart_type="pie",
            ),
        ]
    )
    updated = PresentationPreflight(resolver).normalize(project_id, spec)
    assert [block.type for block in updated.sections[0].blocks] == ["narrative"]


def test_dense_visual_group_stacks_instead_of_two_column(tmp_path: Path) -> None:
    rows = ["category,amount"]
    rows.extend(f"long-category-label-{index},{index}" for index in range(16))
    project_id, resolver = _workspace(tmp_path, "\n".join(rows) + "\n")
    amount = _metric("amount", "currency", "yuan")
    series = [
        SeriesSpec(
            field="amount",
            label="Amount",
            metric="amount",
            metric_definition=amount,
        )
    ]
    spec = _spec(
        [
            VisualGroupBlock(
                type="visual_group",
                layout="two-column",
                items=[_chart(series, "line", "chart_1"), _chart(series, "bar", "chart_2")],
            )
        ]
    )
    updated = PresentationPreflight(resolver).normalize(project_id, spec)
    group = updated.sections[0].blocks[0]
    assert isinstance(group, VisualGroupBlock)
    assert group.layout == "stack"


def test_simple_visual_group_can_stay_two_column(tmp_path: Path) -> None:
    project_id, resolver = _workspace(tmp_path, "category,amount\nA,10\nB,20\n")
    amount = _metric("amount", "currency", "yuan")
    series = [
        SeriesSpec(
            field="amount",
            label="Amount",
            metric="amount",
            metric_definition=amount,
        )
    ]
    spec = _spec(
        [
            VisualGroupBlock(
                type="visual_group",
                layout="two-column",
                items=[_chart(series, "bar", "chart_1"), _chart(series, "bar", "chart_2")],
            )
        ]
    )
    updated = PresentationPreflight(resolver).normalize(project_id, spec)
    group = updated.sections[0].blocks[0]
    assert isinstance(group, VisualGroupBlock)
    assert group.layout == "two-column"


def _charts(spec: ReportSpec) -> list[ChartSpec]:
    return [block.chart for block in spec.sections[0].blocks if isinstance(block, ChartBlock)]


def test_fixture_a_currency_and_count_are_not_coaxis(tmp_path: Path) -> None:
    project_id, resolver = _workspace(tmp_path, "category_a,metric_a,metric_b\nA,10,2\nB,20,3\n")
    spec = _spec(
        [
            _chart(
                [
                    SeriesSpec(
                        field="metric_a",
                        label="Metric A",
                        metric="metric_a",
                        metric_definition=_metric("metric_a", "currency", "u"),
                    ),
                    SeriesSpec(
                        field="metric_b",
                        label="Metric B",
                        metric="metric_b",
                        metric_definition=_metric("metric_b", "count", "items"),
                    ),
                ]
            )
        ]
    )
    charts = _charts(PresentationPreflight(resolver).normalize(project_id, spec))
    assert len(charts) == 2
    families = [{item.metric_definition.unit_family for item in chart.series} for chart in charts]
    assert {"currency"} in families
    assert {"count"} in families
    assert all(item.axis == "left" for chart in charts for item in chart.series)


def test_fixture_b_same_currency_family_can_share_axis(tmp_path: Path) -> None:
    project_id, resolver = _workspace(tmp_path, "category_a,metric_a,metric_b\nA,10,12\nB,20,18\n")
    spec = _spec(
        [
            _chart(
                [
                    SeriesSpec(
                        field="metric_a",
                        label="Metric A",
                        metric="metric_a",
                        metric_definition=_metric("metric_a", "currency", "u"),
                    ),
                    SeriesSpec(
                        field="metric_b",
                        label="Metric B",
                        metric="metric_b",
                        metric_definition=_metric("metric_b", "currency", "u"),
                    ),
                ]
            )
        ]
    )
    charts = _charts(PresentationPreflight(resolver).normalize(project_id, spec))
    assert len(charts) == 1
    assert {item.metric for item in charts[0].series} == {"metric_a", "metric_b"}


def test_fixture_c_same_percentage_family_can_share_axis(tmp_path: Path) -> None:
    project_id, resolver = _workspace(tmp_path, "category_a,rate_a,rate_b\nA,10,12\nB,20,18\n")
    spec = _spec(
        [
            _chart(
                [
                    SeriesSpec(
                        field="rate_a",
                        label="Rate A",
                        metric="rate_a",
                        metric_definition=_metric("rate_a", "percentage", "%"),
                    ),
                    SeriesSpec(
                        field="rate_b",
                        label="Rate B",
                        metric="rate_b",
                        metric_definition=_metric("rate_b", "percentage", "%"),
                    ),
                ]
            )
        ]
    )
    charts = _charts(PresentationPreflight(resolver).normalize(project_id, spec))
    assert len(charts) == 1
    assert len(charts[0].series) == 2


def test_incompatible_series_coverage_collapses_to_complete_series(tmp_path: Path) -> None:
    project_id, resolver = _workspace(
        tmp_path,
        "category_a,overall_rate,dimension_rate\nA,99.9,23.9\nB,,19.9\nC,,17.9\n",
    )
    spec = _spec(
        [
            _chart(
                [
                    SeriesSpec(
                        field="overall_rate",
                        label="Overall Rate",
                        metric="overall_rate",
                        metric_definition=_metric("overall_rate", "percentage", "%"),
                    ),
                    SeriesSpec(
                        field="dimension_rate",
                        label="Dimension Rate",
                        metric="dimension_rate",
                        metric_definition=_metric("dimension_rate", "percentage", "%"),
                    ),
                ],
                chart_type="grouped_bar",
            )
        ]
    )

    charts = _charts(PresentationPreflight(resolver).normalize(project_id, spec))

    assert len(charts) == 1
    assert [series.field for series in charts[0].series] == ["dimension_rate"]


def test_fixture_d_unknown_families_do_not_auto_share_axis(tmp_path: Path) -> None:
    project_id, resolver = _workspace(tmp_path, "category_a,metric_x,metric_y\nA,10,12\nB,20,18\n")
    spec = _spec(
        [
            _chart(
                [
                    SeriesSpec(field="metric_x", label="Metric X", metric="metric_x"),
                    SeriesSpec(field="metric_y", label="Metric Y", metric="metric_y"),
                ]
            )
        ]
    )
    charts = _charts(PresentationPreflight(resolver).normalize(project_id, spec))
    assert len(charts) == 2
    assert [len(chart.series) for chart in charts] == [1, 1]


def test_fixture_e_duration_and_count_are_not_coaxis(tmp_path: Path) -> None:
    project_id, resolver = _workspace(tmp_path, "category_a,duration_a,metric_b\nA,8,2\nB,5,3\n")
    spec = _spec(
        [
            _chart(
                [
                    SeriesSpec(
                        field="duration_a",
                        label="Duration A",
                        metric="duration_a",
                        metric_definition=_metric("duration_a", "duration", "hours"),
                    ),
                    SeriesSpec(
                        field="metric_b",
                        label="Metric B",
                        metric="metric_b",
                        metric_definition=_metric("metric_b", "count", "items"),
                    ),
                ]
            )
        ]
    )
    charts = _charts(PresentationPreflight(resolver).normalize(project_id, spec))
    assert len(charts) == 2
    families = [{item.metric_definition.unit_family for item in chart.series} for chart in charts]
    assert {"duration"} in families
    assert {"count"} in families


def test_combo_incompatible_metrics_are_split_not_dual_axis(tmp_path: Path) -> None:
    project_id, resolver = _workspace(tmp_path, "category_a,metric_a,rate_z\nA,10,12\nB,20,18\n")
    spec = _spec(
        [
            _chart(
                [
                    SeriesSpec(
                        field="metric_a",
                        label="Metric A",
                        metric="metric_a",
                        metric_definition=_metric("metric_a", "currency", "u"),
                    ),
                    SeriesSpec(
                        field="rate_z",
                        label="Rate Z",
                        metric="rate_z",
                        metric_definition=_metric("rate_z", "percentage", "%"),
                    ),
                ],
                chart_type="combo",
            )
        ]
    )
    charts = _charts(PresentationPreflight(resolver).normalize(project_id, spec))
    assert len(charts) == 2
    assert all(chart.chart_type != "combo" for chart in charts)
    assert all(item.axis == "left" for chart in charts for item in chart.series)


def test_unusable_auxiliary_series_collapses_to_primary(tmp_path: Path) -> None:
    project_id, resolver = _workspace(tmp_path, "category_a,metric_a,metric_y\nA,10,12\nB,20,18\n")
    spec = _spec(
        [
            _chart(
                [
                    SeriesSpec(
                        field="metric_a",
                        label="Metric A",
                        metric="metric_a",
                        metric_definition=_metric("metric_a", "currency", "u"),
                    ),
                    SeriesSpec(
                        field="metric_y",
                        label="Metric Y",
                        metric="metric_y",
                        presentation_usable=False,
                    ),
                ]
            )
        ]
    )
    charts = _charts(PresentationPreflight(resolver).normalize(project_id, spec))
    assert len(charts) == 1
    assert [item.metric for item in charts[0].series] == ["metric_a"]


def test_preflight_production_rules_are_generic() -> None:
    source = Path("app/services/presentation_preflight.py").read_text(encoding="utf-8")
    banned = ["成交金额", "成交客户数", "省份", "杭州", "借呗", "销售工号"]
    for token in banned:
        assert token not in source


def test_split_chart_titles_use_single_series_display_labels(tmp_path: Path) -> None:
    project_id, resolver = _workspace(tmp_path, "dimension_a,metric_x,metric_y\nA,10,2\nB,20,3\n")
    metric_x = _metric("metric_x", "currency", "yuan").model_copy(
        update={"label": "Metric X Display"}
    )
    metric_y = _metric("metric_y", "count", "items").model_copy(
        update={"label": "Metric Y Display"}
    )
    spec = _spec(
        [
            _chart(
                [
                    SeriesSpec(
                        field="metric_x",
                        label="metric_x",
                        metric="metric_x",
                        metric_definition=metric_x,
                    ),
                    SeriesSpec(
                        field="metric_y",
                        label="metric_y",
                        metric="metric_y",
                        metric_definition=metric_y,
                    ),
                ]
            )
        ]
    )

    updated = PresentationPreflight(resolver).normalize(project_id, spec)
    charts = [block.chart for block in updated.sections[0].blocks if isinstance(block, ChartBlock)]

    assert [chart.title for chart in charts] == ["Metric X Display", "Metric Y Display"]
    assert all("Mixed series" not in chart.title for chart in charts)
    assert all(
        "metric_x" not in chart.title or chart.title == "Metric X Display" for chart in charts
    )
    assert all(
        "metric_y" not in chart.title or chart.title == "Metric Y Display" for chart in charts
    )


def test_split_chart_titles_fallback_humanizes_field_without_raw_ref(tmp_path: Path) -> None:
    project_id, resolver = _workspace(tmp_path, "dimension_a,metric_x,metric_y\nA,10,2\nB,20,3\n")
    metric_x = _metric("metric_x", "currency", "yuan").model_copy(update={"label": "metric_x"})
    metric_y = _metric("metric_y", "count", "items").model_copy(update={"label": "metric_y"})
    spec = _spec(
        [
            _chart(
                [
                    SeriesSpec(
                        field="metric_x",
                        label="metric_x",
                        metric="metric_x",
                        metric_definition=metric_x,
                    ),
                    SeriesSpec(
                        field="metric_y",
                        label="metric_y",
                        metric="metric_y",
                        metric_definition=metric_y,
                    ),
                ]
            )
        ]
    )

    updated = PresentationPreflight(resolver).normalize(project_id, spec)
    charts = [block.chart for block in updated.sections[0].blocks if isinstance(block, ChartBlock)]

    assert [chart.title for chart in charts] == ["Metric X", "Metric Y"]
