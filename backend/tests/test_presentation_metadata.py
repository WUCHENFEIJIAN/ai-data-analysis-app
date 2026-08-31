import pytest

from app.services.metric_contract import (
    MetricDefinition,
    is_internal_semantic_token,
    metric_display_unit,
)
from app.services.presentation_metadata import PresentationMetadata, PresentationMetadataResolver
from app.services.report_renderer import ReportRenderer, _resolved_column_semantic
from app.services.report_semantics import format_table_value
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
)


def _metric(metric_id: str, unit_family: str, unit: str, value: float = 100) -> MetricDefinition:
    if unit_family == "count":
        semantic = "count"
    elif unit_family == "percentage":
        semantic = "rate"
    else:
        semantic = "measure"
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
        payload.update(count_semantics="field_sum", is_distinct=False)
    return MetricDefinition(**payload)


def _spec(
    series: list[SeriesSpec],
    columns: list[TableColumnSpec],
    kpis: list[KpiSpec],
) -> ReportSpec:
    source = SourceSpec(
        id="src_summary",
        artifact_path="data/summary.csv",
        kind="csv",
        sha256="a" * 64,
        media_type="text/csv",
        usage="visual_source",
    )
    return ReportSpec(
        schema_version="3.0",
        locale="zh-CN",
        analysis_topic="Topic",
        title="Title",
        sources=[source],
        kpis=kpis,
        sections=[
            SectionSpec(
                id="section_1",
                title="Section",
                blocks=[
                    ChartBlock(
                        type="chart",
                        chart=ChartSpec(
                            id="chart_1",
                            chart_type="bar",
                            title="Chart",
                            purpose="Compare",
                            source_id="src_summary",
                            x_field="category",
                            series=series,
                            source_caption="Source",
                        ),
                    ),
                    TableBlock(
                        type="table",
                        id="table_1",
                        source_id="src_summary",
                        title="Table",
                        purpose="Summary",
                        columns=columns,
                    ),
                ],
            )
        ],
        provenance=ProvenanceSpec(planner_mode="fallback"),
    )


def test_same_metric_uses_one_presentation_for_kpi_chart_and_table() -> None:
    metric = _metric("amount", "currency", "yuan", 24475175.8)
    spec = _spec(
        [
            SeriesSpec(
                field="amount",
                label="amount",
                metric="amount",
                metric_definition=metric,
            )
        ],
        [
            TableColumnSpec(field="category", label="Category", format="text"),
            TableColumnSpec(
                field="amount",
                label="amount",
                metric="amount",
                metric_definition=metric,
            ),
        ],
        [
            KpiSpec(
                id="kpi_amount",
                label="Amount",
                metric="amount",
                format="number",
                decimals=0,
                purpose="Scale",
                metric_definition=metric,
            )
        ],
    )
    updated = PresentationMetadataResolver.apply(spec, {"amount": metric})
    kpi = updated.kpis[0]
    series = updated.sections[0].blocks[0].chart.series[0]
    column = updated.sections[0].blocks[1].columns[1]
    assert kpi.format == series.format == "currency"
    assert kpi.unit == series.unit == column.unit == "yuan"
    assert kpi.scale == series.scale == column.scale == 1
    assert kpi.decimals == series.decimals == column.decimals == 2
    assert metric.value == 24475175.8


def test_unknown_measure_field_is_not_guessed() -> None:
    meta = PresentationMetadataResolver.resolve_measure("mystery_value", {})
    assert meta.usable is False
    series = SeriesSpec(field="mystery_value", label="mystery_value", metric="mystery_value")
    spec = _spec(
        [series],
        [TableColumnSpec(field="mystery_value", label="mystery_value", format="number")],
        [],
    )
    updated = PresentationMetadataResolver.apply(spec, {})
    chart_series = updated.sections[0].blocks[0].chart.series[0]
    assert chart_series.presentation_usable is False
    assert chart_series.unit is None
    assert chart_series.format == "number"


def test_count_and_percent_do_not_share_currency_format() -> None:
    count = _metric("orders", "count", "items", 12)
    rate = _metric("share", "percentage", "%", 46.65)
    assert PresentationMetadata.from_metric(count).format_name == "integer"
    assert PresentationMetadata.from_metric(rate).format_name == "percent"
    assert PresentationMetadata.from_metric(count).decimals == 0
    assert PresentationMetadata.from_metric(rate).decimals == 2


def test_renderer_does_not_infer_percentage_without_declared_semantic() -> None:
    column = TableColumnSpec(field="share", label="share", format="text")
    assert _resolved_column_semantic(column, ["0.4665", "0.2"]) == "text"
    renderer = ReportRenderer.__new__(ReportRenderer)
    assert renderer._table_value("0.4665", column, "text") == "0.4665"


def test_fraction_ratio_uses_metric_basis_for_percent_display() -> None:
    rate = MetricDefinition(
        metric_id="late_rate",
        label="Late delivery rate",
        value=0.078466,
        aggregation="ratio",
        semantic_type="rate",
        unit_family="percentage",
        ratio_basis="per_entity",
        ratio_value_basis="fraction",
        numerator="late_orders",
        denominator="orders",
        numerator_value=7.8466,
        denominator_value=100,
        unit="%",
        definition="late_orders / orders",
        source_artifact="data/fulfillment.csv",
    )
    meta = PresentationMetadata.from_metric(rate)
    assert meta.format_name == "percent"
    assert meta.decimals == 2
    assert meta.display_scale == 0.01
    renderer = ReportRenderer.__new__(ReportRenderer)
    assert renderer._format(
        rate.value, meta.format_name, meta.decimals, meta.display_scale
    ) == "7.85%"


def test_percent_ratio_keeps_percent_points_scale() -> None:
    rate = MetricDefinition(
        metric_id="conversion_rate",
        label="Conversion rate",
        value=7.85,
        aggregation="ratio",
        semantic_type="rate",
        unit_family="percentage",
        ratio_basis="per_entity",
        ratio_value_basis="percent",
        numerator="conversions",
        denominator="visits",
        numerator_value=7.85,
        denominator_value=100,
        unit="%",
        definition="conversions / visits",
        source_artifact="data/funnel.csv",
    )
    meta = PresentationMetadata.from_metric(rate)
    assert meta.display_scale == 1
    renderer = ReportRenderer.__new__(ReportRenderer)
    assert renderer._format(
        rate.value, meta.format_name, meta.decimals, meta.display_scale
    ) == "7.85%"


def test_denominator_basis_does_not_control_percentage_display_scale() -> None:
    rate = MetricDefinition(
        metric_id="late_rate",
        label="Late rate",
        value=0.081,
        aggregation="ratio",
        semantic_type="rate",
        unit_family="percentage",
        ratio_basis="per_entity",
        numerator="late_orders",
        denominator="orders",
        numerator_value=81,
        denominator_value=1000,
        unit="%",
        definition="late_orders / orders",
        source_artifact="data/fulfillment.csv",
    )

    meta = PresentationMetadata.from_metric(rate)
    assert meta.ratio_basis == "per_entity"
    assert meta.ratio_value_basis == "fraction"
    assert meta.display_scale == 0.01
    updated = PresentationMetadataResolver.apply(
        _spec(
            [SeriesSpec(field="late_rate", label="Late rate", metric="late_rate")],
            [TableColumnSpec(field="late_rate", label="Late rate", metric="late_rate")],
            [],
        ),
        {"late_rate": rate},
    )
    assert updated.sections[0].blocks[0].chart.series[0].scale == 0.01
    assert updated.sections[0].blocks[1].columns[0].scale == 0.01


def _percentage_metric(
    metric_id: str,
    value: float,
    value_basis: str,
    unit: str,
) -> MetricDefinition:
    """Percentage metric whose contract unit carries an internal representation name."""

    return MetricDefinition(
        metric_id=metric_id,
        label=metric_id.replace("_", " ").title(),
        value=value,
        aggregation="ratio",
        semantic_type="rate",
        unit_family="percentage",
        ratio_basis="per_entity",
        ratio_value_basis=value_basis,
        numerator="numerator_metric",
        denominator="denominator_metric",
        unit=unit,
        definition="numerator_metric / denominator_metric",
        source_artifact="data/rates.json",
    )


def _percentage_spec(metric: MetricDefinition) -> ReportSpec:
    return _spec(
        [
            SeriesSpec(
                field=metric.metric_id,
                label=metric.label,
                metric=metric.metric_id,
                metric_definition=metric,
            )
        ],
        [
            TableColumnSpec(
                field=metric.metric_id,
                label=metric.label,
                metric=metric.metric_id,
                metric_definition=metric,
            )
        ],
        [
            KpiSpec(
                id="kpi_rate",
                label=metric.label,
                metric=metric.metric_id,
                format="number",
                decimals=0,
                purpose="Track the rate",
                metric_definition=metric,
            )
        ],
    )


@pytest.mark.parametrize(
    "value_basis,contract_unit,expected",
    [
        ("fraction", "fraction", "2.98%"),
        ("percent", "percent", "8.10%"),
    ],
)
def test_internal_value_basis_never_reaches_kpi_display_string(
    value_basis: str, contract_unit: str, expected: str
) -> None:
    """A representation name must not be used as a user-visible unit suffix."""

    value = 0.0298 if value_basis == "fraction" else 8.1
    metric = _percentage_metric("customer_repeat_rate", value, value_basis, contract_unit)
    meta = PresentationMetadata.from_metric(metric)

    assert meta.ratio_value_basis == value_basis
    assert meta.display_unit == ""
    assert meta.canonical_unit == contract_unit

    renderer = ReportRenderer.__new__(ReportRenderer)
    rendered = renderer._format(metric.value, meta.format_name, meta.decimals, meta.display_scale)
    rendered += meta.display_unit
    assert rendered == expected
    assert "fraction" not in rendered
    assert "percent" not in rendered


def test_percentage_display_semantics_are_shared_by_kpi_chart_and_table() -> None:
    metric = _percentage_metric("customer_repeat_rate", 0.0298, "fraction", "fraction")
    updated = PresentationMetadataResolver.apply(
        _percentage_spec(metric), {metric.metric_id: metric}
    )
    kpi = updated.kpis[0]
    series = updated.sections[0].blocks[0].chart.series[0]
    column = updated.sections[0].blocks[1].columns[0]

    assert kpi.format == series.format == "percent"
    assert kpi.decimals == series.decimals == column.decimals == 2
    assert kpi.scale == series.scale == column.scale == 0.01
    assert kpi.unit is None and series.unit is None and column.unit is None
    assert column.semantic_type == "percentage_fraction"

    renderer = ReportRenderer.__new__(ReportRenderer)
    kpi_text = renderer._format(
        metric.value, kpi.format, kpi.decimals, kpi.scale
    ) + (kpi.unit or "")
    table_text = format_table_value(
        metric.value,
        column.semantic_type,
        format_name=column.format,
        decimals=column.decimals,
        unit=column.unit,
        scale=column.scale,
    )
    axis_text = renderer._format(metric.value, series.format, series.decimals, series.scale) + (
        series.unit or ""
    )
    assert kpi_text == table_text == axis_text == "2.98%"


@pytest.mark.parametrize(
    "unit_family,contract_unit",
    [
        ("count", "count"),
        ("count", "distinct_count"),
        ("currency", "currency"),
        ("duration", "duration"),
        ("ratio", "ratio"),
        ("score", "score"),
        ("quantity", "quantity"),
    ],
)
def test_internal_semantic_tokens_are_rejected_as_display_units(
    unit_family: str, contract_unit: str
) -> None:
    metric = _metric("generic_measure", unit_family, contract_unit, 12)
    assert is_internal_semantic_token(contract_unit) is True
    assert metric_display_unit(metric) == ""
    assert PresentationMetadata.from_metric(metric).display_unit == ""


@pytest.mark.parametrize("display_unit", ["个对象A", "万元", "USD", "days", "件"])
def test_authored_display_units_are_preserved(display_unit: str) -> None:
    metric = _metric("generic_measure", "count", display_unit, 12)
    assert is_internal_semantic_token(display_unit) is False
    assert PresentationMetadata.from_metric(metric).display_unit == display_unit
