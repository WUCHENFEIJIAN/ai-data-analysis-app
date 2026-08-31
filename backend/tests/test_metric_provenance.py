from app.schemas.actions import AgentActionResponse
from app.services.metric_provenance import validate_metric_provenance
from app.services.workspace import PathResolver


def _action(*, value: float = 120, source_artifact: str = "data/dedup.csv"):
    return AgentActionResponse.model_validate(
        {
            "action": "complete_analysis",
            "summary": "Verified category count",
            "metrics": [
                {
                    "metric_id": "category_a_count",
                    "metric_scope": "scalar_evidence",
                    "label": "Category A count",
                    "value": value,
                    "aggregation": "sum",
                    "semantic_type": "count",
                    "unit_family": "count",
                    "count_semantics": "field_sum",
                    "is_distinct": False,
                    "grain": "payment_record",
                    "definition": "Count for category A",
                    "source_artifact": source_artifact,
                    "source_field": "count",
                    "source_selector": {"category": "A"},
                },
                {
                    "metric_id": "category_count",
                    "metric_scope": "reusable_measure",
                    "label": "Category count",
                    "value": 100,
                    "aggregation": "sum",
                    "semantic_type": "count",
                    "unit_family": "count",
                    "count_semantics": "field_sum",
                    "is_distinct": False,
                    "grain": "payment_record",
                    "definition": "Count by category",
                    "source_artifact": "data/dedup.csv",
                },
            ],
            "findings": [
                {
                    "id": "finding_category_a",
                    "title": "Category A has a verified count",
                    "evidence": ["Category A count is verified"],
                    "risk": "Counts depend on the declared grain",
                    "recommendation": "Monitor the same grain",
                    "related_artifacts": ["data/dedup.csv"],
                    "claims": [
                        {
                            "claim_id": "claim_category_a",
                            "statement": f"Category A count is {value}",
                            "evidence_metric_ids": ["category_a_count"],
                            "evidence_artifact_paths": [source_artifact],
                        }
                    ],
                }
            ],
            "report_ready_artifacts": [
                {
                    "artifact_path": "data/dedup.csv",
                    "fields": [
                        {"name": "category", "role": "dimension"},
                        {
                            "name": "count",
                            "role": "measure",
                            "metric_ref": "category_count",
                        },
                    ],
                }
            ],
        }
    ).root


def _validate(resolver: PathResolver, project_id: str, action) -> list[dict]:
    return validate_metric_provenance(
        resolver,
        project_id,
        action.findings,
        action.metrics,
        action.report_ready_artifacts,
    )


def test_scalar_value_cannot_claim_inconsistent_dedup_artifact(tmp_path) -> None:
    project_id = "pj_" + "a" * 32
    resolver = PathResolver(tmp_path)
    data = resolver.resolve(project_id, "data/dedup.csv")
    data.parent.mkdir(parents=True)
    data.write_text("category,count\nA,100\nB,50\n", encoding="utf-8")
    raw = resolver.resolve(project_id, "data/raw.csv")
    raw.write_text("category,count\nA,120\nB,60\n", encoding="utf-8")

    mismatch = _validate(resolver, project_id, _action())
    assert mismatch[0]["code"] == "METRIC_PROVENANCE_VALUE_MISMATCH"
    assert mismatch[0]["declared_value"] == 120
    assert mismatch[0]["reproduced_value"] == 100

    assert _validate(resolver, project_id, _action(value=100)) == []
    assert _validate(resolver, project_id, _action(source_artifact="data/raw.csv")) == []


def test_direct_report_ready_scalar_requires_reproducible_selector(tmp_path) -> None:
    project_id = "pj_" + "b" * 32
    resolver = PathResolver(tmp_path)
    data = resolver.resolve(project_id, "data/dedup.csv")
    data.parent.mkdir(parents=True)
    data.write_text("category,count\nA,100\nB,50\n", encoding="utf-8")
    action = _action(value=100)
    action.metrics[0].source_selector = None

    issues = _validate(resolver, project_id, action)

    assert issues[0]["code"] == "METRIC_PROVENANCE_UNVERIFIABLE"


def _object_action(*, value: float, source_field: str):
    action = _action(value=value, source_artifact="data/summary.json")
    action.metrics[0].source_field = source_field
    action.metrics[0].source_selector = None
    action.report_ready_artifacts[0].artifact_path = "data/summary.json"
    return action


def test_top_level_json_scalar_is_verified_without_tabular_records(tmp_path) -> None:
    project_id = "pj_" + "c" * 32
    resolver = PathResolver(tmp_path)
    summary = resolver.resolve(project_id, "data/summary.json")
    summary.parent.mkdir(parents=True)
    summary.write_text('{"metric_x":100}', encoding="utf-8")

    assert _validate(
        resolver, project_id, _object_action(value=100, source_field="metric_x")
    ) == []


def test_top_level_json_scalar_rejects_wrong_declared_value(tmp_path) -> None:
    project_id = "pj_" + "d" * 32
    resolver = PathResolver(tmp_path)
    summary = resolver.resolve(project_id, "data/summary.json")
    summary.parent.mkdir(parents=True)
    summary.write_text('{"metric_x":100}', encoding="utf-8")

    issues = _validate(
        resolver, project_id, _object_action(value=120, source_field="metric_x")
    )

    assert issues[0]["code"] == "METRIC_PROVENANCE_VALUE_MISMATCH"
    assert issues[0]["declared_value"] == 120
    assert issues[0]["reproduced_value"] == 100


def test_derived_scalar_uses_materialized_nested_summary_value(tmp_path) -> None:
    project_id = "pj_" + "e" * 32
    resolver = PathResolver(tmp_path)
    summary = resolver.resolve(project_id, "data/summary.json")
    summary.parent.mkdir(parents=True)
    summary.write_text(
        '{"changes":{"amount_change":7792942.17}}', encoding="utf-8"
    )

    assert _validate(
        resolver,
        project_id,
        _object_action(value=7792942.17, source_field="changes.amount_change"),
    ) == []


def test_artifact_mismatch_includes_compact_source_repair_context(tmp_path) -> None:
    project_id = "pj_" + "f" * 32
    resolver = PathResolver(tmp_path)
    summary = resolver.resolve(project_id, "data/summary.json")
    summary.parent.mkdir(parents=True)
    summary.write_text('{"metric_x":100,"nested":{"metric_y":42}}', encoding="utf-8")
    action = _object_action(value=100, source_field="metric_x")
    action.findings[0].claims[0].evidence_artifact_paths = ["data/other.csv"]
    action.report_ready_artifacts[0].artifact_path = "data/other.csv"

    issues = _validate(resolver, project_id, action)

    assert issues[0]["code"] == "METRIC_PROVENANCE_ARTIFACT_MISMATCH"
    assert issues[0]["source_artifact"] == "data/summary.json"
    assert issues[0]["related_artifacts"] == ["data/other.csv"]
    assert issues[0]["declared_value"] == 100
    assert issues[0]["observed_value"] == 100
    assert issues[0]["available_value_paths"] == ["metric_x", "nested.metric_y"]
