from app.services.metric_contract import MetricDefinition


def _metric(**overrides):
    payload = {
        "metric_id": "metric_x",
        "label": "Metric X",
        "value": 100,
        "aggregation": "sum",
        "semantic_type": "measure",
        "unit_family": "currency",
        "definition": "Sum of Metric X",
        "source_artifact": "data/category_metrics.csv",
    }
    payload.update(overrides)
    return MetricDefinition.model_validate(payload)


def test_legacy_metric_defaults_to_scalar_evidence():
    metric = _metric()

    assert metric.metric_scope == "scalar_evidence"
    assert metric.model_dump(mode="json")["metric_scope"] == "scalar_evidence"


def test_reusable_measure_scope_is_explicit_and_canonical():
    metric = _metric(metric_scope="reusable_measure")

    assert metric.metric_scope == "reusable_measure"
    assert metric.model_dump(mode="json")["metric_scope"] == "reusable_measure"


def test_metric_scope_schema_explains_neutral_measure_and_scalar_usage():
    property_schema = MetricDefinition.model_json_schema()["properties"]["metric_scope"]

    assert property_schema["default"] == "scalar_evidence"
    assert set(property_schema["enum"]) == {"reusable_measure", "scalar_evidence"}
    assert "dimension values" in property_schema["description"]
