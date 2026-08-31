import pytest
from pydantic import ValidationError

from app.services.metric_contract import (
    MetricDefinition,
    MetricValidationError,
    MetricValidator,
    metric_ratio_value_basis,
    metric_reference_issues,
)
from app.services.report_evidence import (
    REPORT_EVIDENCE_GUIDANCE,
    ReportEvidenceManifest,
    manifest_metric_reference_issues,
)


def _metric_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "metric_id": "total_value",
        "label": "Total value",
        "value": 100,
        "aggregation": "sum",
        "semantic_type": "measure",
        "unit_family": "currency",
        "definition": "Sum of verified values",
        "source_artifact": "data/summary.json",
    }
    payload.update(overrides)
    return payload


def test_metric_definition_accepts_canonical_metric_id() -> None:
    metric = MetricDefinition.model_validate(_metric_payload())

    assert metric.metric_id == "total_value"
    assert metric.model_dump()["metric_id"] == "total_value"


def test_metric_definition_normalizes_id_input_alias_to_metric_id() -> None:
    payload = _metric_payload()
    payload["id"] = payload.pop("metric_id")

    metric = MetricDefinition.model_validate(payload)
    serialized = metric.model_dump()

    assert metric.metric_id == "total_value"
    assert serialized["metric_id"] == "total_value"
    assert "id" not in serialized


def test_metric_definition_schema_exposes_only_canonical_identifier() -> None:
    properties = MetricDefinition.model_json_schema()["properties"]

    assert "metric_id" in properties
    assert "id" not in properties
    assert "Always emit this field as 'metric_id'" in properties["metric_id"]["description"]


def test_metric_definition_rejects_id_and_metric_id_together() -> None:
    with pytest.raises(ValidationError, match="cannot contain both 'metric_id'.*'id'"):
        MetricDefinition.model_validate(_metric_payload(id="legacy_value"))


def test_metric_definition_still_rejects_other_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        MetricDefinition.model_validate(_metric_payload(unknown_field="not allowed"))


def test_metric_id_alias_does_not_bypass_semantic_or_provenance_validation() -> None:
    invalid_semantics = _metric_payload(
        aggregation="ratio",
        semantic_type="ratio",
        unit_family="percentage",
        ratio_basis="other",
        numerator="missing_part",
        denominator="missing_total",
        definition="missing_part / missing_total",
    )
    invalid_semantics["id"] = "share"
    del invalid_semantics["metric_id"]
    metric = MetricDefinition.model_validate(invalid_semantics)

    with pytest.raises(MetricValidationError, match="unknown numerator metric"):
        MetricValidator.validate([metric])

    missing_provenance = _metric_payload()
    missing_provenance["id"] = "unproven"
    del missing_provenance["metric_id"]
    missing_provenance.pop("source_artifact")
    with pytest.raises(ValidationError, match="source_artifact"):
        MetricDefinition.model_validate(missing_provenance)


def test_customer_average_and_transaction_average_are_distinct() -> None:
    total = MetricDefinition(
        metric_id="total_sales_amount",
        label="成交金额",
        value=24516718,
        aggregation="sum",
        semantic_type="measure",
        unit_family="currency",
        unit="元",
        definition="成交金额合计",
        source_artifact="analysis/kpi_evidence.json",
    )
    orders = MetricDefinition(
        metric_id="total_transaction_count",
        label="成交笔数",
        value=1657,
        aggregation="count",
        semantic_type="count",
        unit_family="count",
        count_semantics="row_count",
        is_distinct=False,
        unit="笔",
        definition="成交记录数",
        source_artifact="analysis/kpi_evidence.json",
    )
    customers = MetricDefinition(
        metric_id="total_customer_count",
        label="成交客户数",
        value=2205,
        aggregation="distinct_count",
        semantic_type="count",
        unit_family="count",
        count_semantics="distinct_count",
        is_distinct=True,
        unit="人",
        definition="成交客户去重计数",
        source_artifact="analysis/kpi_evidence.json",
    )
    transaction_average = MetricDefinition(
        metric_id="avg_amount_per_transaction",
        label="单笔平均成交金额",
        value=24516718 / 1657,
        aggregation="ratio",
        semantic_type="ratio",
        unit_family="currency",
        numerator="total_sales_amount",
        denominator="total_transaction_count",
        ratio_basis="per_event",
        unit="元",
        definition="成交金额 / 成交笔数",
        source_artifact="analysis/kpi_evidence.json",
    )
    customer_average = MetricDefinition(
        metric_id="avg_revenue_per_customer",
        label="加权客单价",
        value=24516718 / 2205,
        aggregation="ratio",
        semantic_type="ratio",
        unit_family="currency",
        numerator="total_sales_amount",
        denominator="total_customer_count",
        ratio_basis="per_entity",
        unit="元",
        definition="成交金额 / 成交客户数",
        source_artifact="analysis/kpi_evidence.json",
    )

    MetricValidator.validate(
        [total, orders, customers, transaction_average, customer_average]
    )
    assert transaction_average.value != customer_average.value


def test_metric_validator_rejects_customer_label_with_transaction_denominator() -> None:
    denominator = MetricDefinition(
        metric_id="event_count",
        label="Event count",
        value=10,
        aggregation="count",
        semantic_type="count",
        unit_family="count",
        count_semantics="row_count",
        is_distinct=False,
        unit="events",
        definition="Count of event rows",
        source_artifact="analysis/kpi_evidence.json",
    )
    metric = MetricDefinition(
        metric_id="avg_revenue_per_customer",
        label="客单价",
        value=100,
        aggregation="ratio",
        semantic_type="ratio",
        unit_family="currency",
        numerator_value=1000,
        denominator_value=10,
        numerator="total_sales_amount",
        denominator="event_count",
        ratio_basis="per_entity",
        unit="元",
        definition="成交金额 / 成交笔数",
        source_artifact="analysis/kpi_evidence.json",
    )
    with pytest.raises(MetricValidationError, match="per_entity ratio"):
        MetricValidator.validate([denominator, metric])


def test_metric_validator_rejects_ratio_value_mismatch() -> None:
    metric = MetricDefinition(
        metric_id="avg_revenue_per_customer",
        label="加权客单价",
        value=14795.85,
        aggregation="ratio",
        semantic_type="ratio",
        unit_family="currency",
        numerator_value=24516718,
        denominator_value=2205,
        numerator="total_sales_amount",
        denominator="total_customer_count",
        ratio_basis="per_entity",
        unit="元",
        definition="成交金额 / 成交客户数",
        source_artifact="analysis/kpi_evidence.json",
    )
    with pytest.raises(MetricValidationError, match="actual derived numerator metric"):
        MetricValidator.validate([metric])


def test_percentage_ratio_fraction_and_percent_representations_are_consistent() -> None:
    fraction = MetricDefinition(
        metric_id="fraction_rate",
        label="Fraction rate",
        value=0.078466,
        aggregation="ratio",
        semantic_type="rate",
        unit_family="percentage",
        numerator="events",
        denominator="entities",
        numerator_value=78.466,
        denominator_value=1000,
        ratio_basis="per_entity",
        ratio_value_basis="fraction",
        definition="events / entities",
        source_artifact="data/neutral.csv",
    )
    percent = fraction.model_copy(
        update={
            "metric_id": "percent_rate",
            "label": "Percent rate",
            "value": 8.10,
            "numerator_value": 81,
            "ratio_value_basis": "percent",
        }
    )

    MetricValidator.validate([fraction, percent])
    assert metric_ratio_value_basis(fraction) == "fraction"
    assert metric_ratio_value_basis(percent) == "percent"


def test_percentage_ratio_rejects_value_inconsistent_with_representation() -> None:
    metric = MetricDefinition(
        metric_id="late_rate",
        label="Late rate",
        value=0.081,
        aggregation="ratio",
        semantic_type="rate",
        unit_family="percentage",
        numerator="late_orders",
        denominator="orders",
        numerator_value=81,
        denominator_value=1000,
        ratio_basis="per_entity",
        ratio_value_basis="percent",
        definition="late_orders / orders",
        source_artifact="data/neutral.csv",
    )

    with pytest.raises(MetricValidationError, match="does not match numerator / denominator 8.1"):
        MetricValidator.validate([metric])


def test_legacy_percentage_ratio_basis_normalizes_to_value_basis() -> None:
    metric = MetricDefinition.model_validate(
        _metric_payload(
            value=0.42,
            aggregation="mean",
            semantic_type="rate",
            unit_family="percentage",
            ratio_basis="fraction",
        )
    )

    assert metric.ratio_basis is None
    assert metric.ratio_value_basis == "fraction"


def test_metric_validator_gives_actionable_basis_repair_for_measure_ratio() -> None:
    total = MetricDefinition(
        metric_id="total_value",
        label="Total value",
        value=200,
        aggregation="sum",
        semantic_type="measure",
        unit_family="currency",
        definition="Sum of value",
        source_artifact="data/summary.json",
    )
    share = MetricDefinition(
        metric_id="segment_share",
        label="Segment share",
        value=0.25,
        aggregation="ratio",
        semantic_type="ratio",
        unit_family="percentage",
        numerator_value=50,
        denominator="total_value",
        numerator="segment_value",
        ratio_basis="per_event",
        definition="Segment value / total value",
        source_artifact="data/summary.json",
    )

    with pytest.raises(MetricValidationError, match="ratio_basis 'other'"):
        MetricValidator.validate([total, share])


def test_report_evidence_guidance_explains_derived_ratio_contract() -> None:
    assert "per_entity requires an entity or" in REPORT_EVIDENCE_GUIDANCE
    assert "declare a separate delta numerator metric" in REPORT_EVIDENCE_GUIDANCE
    assert "Every Metric Definition uses metric_id" in REPORT_EVIDENCE_GUIDANCE
    assert '"metric_id": "total_value"' in REPORT_EVIDENCE_GUIDANCE
    assert "ratio_basis describes denominator semantics only" in REPORT_EVIDENCE_GUIDANCE
    assert "ratio_value_basis" in REPORT_EVIDENCE_GUIDANCE


def test_metric_definition_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        MetricDefinition(
            metric_id="invalid_metric",
            label="Invalid",
            value=float("nan"),
            aggregation="sum",
            semantic_type="measure",
            unit_family="quantity",
            definition="Precomputed value",
            source_artifact="analysis/kpi_evidence.json",
        )


def test_count_metric_requires_source_count_semantics() -> None:
    with pytest.raises(ValueError, match="count_semantics"):
        MetricDefinition(
            metric_id="entities",
            label="Entities",
            value=12,
            aggregation="sum",
            semantic_type="count",
            unit_family="count",
            unit="items",
            definition="Sum of a source count field",
            source_artifact="analysis/entities.json",
        )


def test_field_sum_count_is_not_distinct() -> None:
    metric = MetricDefinition(
        metric_id="reported_entities",
        label="Reported entities",
        value=12,
        aggregation="sum",
        semantic_type="count",
        unit_family="count",
        count_semantics="field_sum",
        is_distinct=False,
        unit="items",
        definition="Sum of the source count field",
        source_artifact="analysis/entities.json",
    )
    assert metric.count_semantics == "field_sum"
    assert metric.is_distinct is False


def _measure(metric_id: str, *, scale: float = 1, unit: str = "") -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        label=metric_id,
        value=17000000,
        aggregation="sum",
        semantic_type="measure",
        unit_family="currency",
        scale=scale,
        unit=unit,
        definition="Precomputed revenue",
        source_artifact="data/yearly_sales.csv",
    )


def test_metric_reference_issues_include_kpi_and_metric_identity() -> None:
    registry = {"revenue_2020": _measure("revenue_2020", scale=1)}
    issues = metric_reference_issues(
        owner="KPI kpi_revenue_2020",
        metric_id="revenue_2020",
        unit="",
        scale=1000000,
        registry=registry,
    )
    assert issues
    assert "KPI kpi_revenue_2020" in issues[0]
    assert "revenue_2020" in issues[0]
    assert "1000000" in issues[0]
    assert "1" in issues[0]


def test_manifest_metric_reference_rejects_kpi_scale_mismatch() -> None:
    manifest = ReportEvidenceManifest.model_validate(
        {
            "schema_version": "1.0",
            "metrics": [_measure("revenue_2020").model_dump(mode="json")],
            "kpis": [
                {
                    "id": "kpi_revenue_2020",
                    "label": "2020 revenue",
                    "metric": "revenue_2020",
                    "artifact_path": "data/yearly_sales.csv",
                    "selector": {"type": "json", "path": ["revenue"]},
                    "format": "currency",
                    "scale": 1000000,
                    "purpose": "Show 2020 revenue",
                    "presentation_roles": ["overview"],
                }
            ],
        }
    )
    issues = manifest_metric_reference_issues(manifest)
    assert any("KPI kpi_revenue_2020" in item and "scale 1000000" in item for item in issues)


def test_materialized_share_without_operands_is_valid() -> None:
    metric = MetricDefinition.model_validate(
        {
            "metric_id": "top5_share",
            "metric_scope": "scalar_evidence",
            "label": "Top 5 share",
            "value": 0.25,
            "aggregation": "share",
            "semantic_type": "rate",
            "unit_family": "percentage",
            "ratio_value_basis": "fraction",
            "definition": "Top 5 amount / total amount",
            "source_artifact": "data/scalars.json",
            "source_field": "concentration.top5_share",
        }
    )

    MetricValidator.validate([metric])
