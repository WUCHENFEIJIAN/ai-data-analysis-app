"""Generic grain and count-semantics propagation tests."""

from __future__ import annotations

from dataclasses import replace

from app.services.metric_contract import MetricDefinition
from app.services.report_editor_prompt import ReportEditorPromptLoader
from app.services.report_editorial_context import EditorialContextBuilder
from tests.test_report_metric_fidelity import _inputs


def _count(metric_id: str, count_semantics: str, grain: str) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        metric_scope="reusable_measure",
        label=metric_id,
        value=100,
        aggregation="count",
        semantic_type="count",
        unit_family="count",
        unit="count",
        count_semantics=count_semantics,
        is_distinct=count_semantics in {"distinct_count", "entity_count"},
        grain=grain,
        definition=f"Verified {count_semantics} at {grain} grain",
        source_artifact="data/visual.csv",
        source_field="value_x",
    )


def test_neutral_metrics_preserve_grain_and_count_semantics() -> None:
    inputs = replace(
        _inputs(),
        metrics=[
            _count("record_count", "row_count", "record"),
            _count("entity_count", "entity_count", "entity"),
            _count("event_count", "event_count", "event"),
        ],
    )
    context = EditorialContextBuilder.build(inputs)
    by_id = {item["metric_id"]: item for item in context["metrics"]}

    assert by_id["record_count"]["grain"] == "record"
    assert by_id["record_count"]["count_semantics"] == "row_count"
    assert by_id["entity_count"]["grain"] == "entity"
    assert by_id["entity_count"]["count_semantics"] == "entity_count"
    assert by_id["event_count"]["grain"] == "event"
    assert by_id["event_count"]["count_semantics"] == "event_count"


def test_visual_context_preserves_metric_grain() -> None:
    base = _inputs()
    metric = _count("record_count", "row_count", "record")
    inputs = replace(base, metrics=[metric])
    inputs.evidence_manifest.metrics = [metric]
    inputs.evidence_manifest.artifacts[0].chart.series[0].metric = "record_count"
    inputs.evidence_manifest.artifacts[0].chart.series[0].field = "value_x"

    visual = EditorialContextBuilder.build(inputs)["visuals"][0]
    series = visual["series"][0]
    assert series["metric_ref"] == "record_count"
    assert series["grain"] == "record"
    assert series["count_semantics"] == "row_count"


def test_prompt_requires_grain_fidelity_for_all_narrative_surfaces() -> None:
    prompt = ReportEditorPromptLoader().load()

    assert "grain" in prompt
    assert "count_semantics" in prompt
    assert "Section title" in prompt
    assert "Lead" in prompt
    assert "Interpretation" in prompt
    assert "Executive Summary" in prompt
    assert "record-level" in prompt
    assert "entity-level" in prompt
    assert "记录" in prompt
    assert "不要擅自猜测" in prompt


def test_metric_grain_is_optional_for_legacy_inputs() -> None:
    metric = _inputs().metrics[0]
    assert metric.grain is None
    assert metric.model_copy().grain is None
