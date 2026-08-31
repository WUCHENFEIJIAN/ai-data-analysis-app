"""Field semantic metadata must reach the Report Editor unchanged."""

from __future__ import annotations

from app.schemas.findings import Findings
from app.services.metric_contract import MetricDefinition
from app.services.report_editor_prompt import ReportEditorPromptLoader
from app.services.report_editorial_context import EditorialContextBuilder
from app.services.report_inputs import ArtifactEntry, ReportInputs


def _finding() -> dict:
    return {
        "id": "finding_pattern",
        "title": "Category_a leads metric_x",
        "evidence": ["Computed from analysis artifacts."],
        "risk": "Interpretation depends on current coverage.",
        "recommendation": "Review the leading class.",
        "related_artifacts": ["data/summary.json"],
        "claims": [
            {
                "claim_id": "claim_pattern",
                "statement": "category_a leads metric_x.",
                "priority": "primary",
                "narrative_role": "breakdown",
                "strength": 0.8,
                "evidence_metric_ids": ["metric_x"],
                "evidence_artifact_paths": ["data/summary.json"],
            }
        ],
    }


def _count_metric(metric_id: str, semantics: str, value: float, distinct: bool) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        label=metric_id,
        value=value,
        aggregation="count",
        semantic_type="count",
        unit_family="count",
        unit="count",
        count_semantics=semantics,
        is_distinct=distinct,
        definition=f"Precomputed {semantics}",
        source_artifact="data/summary.json",
    )


def _measure(metric_id: str, aggregation: str, unit_family: str = "quantity") -> MetricDefinition:
    payload = {
        "metric_id": metric_id,
        "label": metric_id,
        "value": 12,
        "aggregation": aggregation,
        "semantic_type": "rate" if unit_family == "percentage" else "measure",
        "unit_family": unit_family,
        "unit": "%" if unit_family == "percentage" else "u",
        "definition": f"Precomputed {aggregation} {metric_id}",
        "source_artifact": "data/summary.json",
    }
    return MetricDefinition(**payload)


def _inputs(metrics: list[MetricDefinition], columns: list[dict] | None = None) -> ReportInputs:
    catalog = []
    if columns is not None:
        catalog = [
            ArtifactEntry(
                id="src_summary",
                path="data/summary.json",
                kind="json",
                sha256="a" * 64,
                media_type="application/json",
                size_bytes=12,
                structure={"record_kind": "table", "columns": columns, "row_count": 3},
            )
        ]
    return ReportInputs(
        analysis_topic="Operations review",
        title="Operations review",
        subtitle=None,
        requested_style=None,
        user_request="Analyze current operations",
        dataset_profile={"file_count": 1},
        analysis_plan={"objective": "Review operations"},
        findings=Findings.model_validate({"summary": "Summary.", "findings": [_finding()]}),
        metrics=metrics,
        catalog=catalog,
    )


def _by_id(context: dict, metric_id: str) -> dict:
    return next(item for item in context["metrics"] if item["metric_id"] == metric_id)


def test_case_a_record_count_is_not_entity_count() -> None:
    context = EditorialContextBuilder.build(
        _inputs(
            [
                _count_metric("record_count", "row_count", 1000, False),
                _count_metric("entity_count", "entity_count", 50, True),
            ]
        )
    )
    record = _by_id(context, "record_count")
    entity = _by_id(context, "entity_count")
    assert record["count_semantics"] == "row_count"
    assert record["value"] == 1000
    assert entity["count_semantics"] == "entity_count"
    assert entity["value"] == 50
    assert record["count_semantics"] != entity["count_semantics"]


def test_case_b_event_count_is_not_user_count() -> None:
    context = EditorialContextBuilder.build(
        _inputs(
            [
                _count_metric("event_count", "event_count", 80, False),
                _count_metric("user_count", "entity_count", 12, True),
            ]
        )
    )
    assert _by_id(context, "event_count")["count_semantics"] == "event_count"
    assert _by_id(context, "user_count")["count_semantics"] == "entity_count"


def test_case_c_amount_share_is_not_record_share() -> None:
    context = EditorialContextBuilder.build(
        _inputs(
            [
                _measure("amount_share", "share", "percentage"),
                _measure("record_share", "share", "percentage"),
            ]
        )
    )
    assert _by_id(context, "amount_share")["display_label"] == "amount_share"
    assert _by_id(context, "record_share")["display_label"] == "record_share"
    assert _by_id(context, "amount_share")["aggregation"] == "share"
    assert _by_id(context, "record_share")["aggregation"] == "share"


def test_case_d_identifier_count_is_not_record_count() -> None:
    context = EditorialContextBuilder.build(
        _inputs(
            [
                _count_metric("identifier_count", "distinct_count", 9, True),
                _count_metric("record_count", "row_count", 1000, False),
            ]
        )
    )
    assert _by_id(context, "identifier_count")["count_semantics"] == "distinct_count"
    assert _by_id(context, "record_count")["count_semantics"] == "row_count"


def test_case_e_average_metric_is_not_total_metric() -> None:
    context = EditorialContextBuilder.build(
        _inputs(
            [
                _measure("average_metric_x", "mean"),
                _measure("total_metric_x", "sum"),
            ]
        )
    )
    assert _by_id(context, "average_metric_x")["aggregation"] == "mean"
    assert _by_id(context, "total_metric_x")["aggregation"] == "sum"


def test_artifact_columns_keep_semantic_type_and_display_label() -> None:
    context = EditorialContextBuilder.build(
        _inputs(
            [],
            columns=[
                {
                    "name": "identifier_a",
                    "dtype": "string",
                    "semantic_type": "identifier",
                    "display_label": "identifier_a",
                }
            ],
        )
    )
    column = context["artifact_catalog"][0]["columns"][0]
    assert column["semantic_type"] == "identifier"
    assert column["display_label"] == "identifier_a"


def test_prompt_documents_field_semantic_fidelity() -> None:
    prompt = ReportEditorPromptLoader().load()
    assert "字段语义保真" in prompt
    assert "不得为了语言自然度" in prompt
    assert "record_count 不得解释成 entity_count" in prompt
    assert "共有1000个实体" in prompt
    assert "共有1000条记录" in prompt
    assert "event_count 与 user_count 不得互换" in prompt
    assert "amount_share 与 record_share 不得互换" in prompt
    assert "average_metric_x 与 total_metric_x 不得互换" in prompt


def test_prompt_documents_interpretation_so_what() -> None:
    prompt = ReportEditorPromptLoader().load()
    assert "So what" in prompt
    assert "只引用支撑判断所必需的关键数值" in prompt
    assert "不要逐项重新转录全部数据" in prompt
    assert "category_a = 48%" in prompt
    assert "A为48%，B为31%，C为20%。" in prompt
    assert "不能只是“图表展示了" in prompt or '不能只是"图表展示了' in prompt


def test_production_editorial_rules_are_generic() -> None:
    from pathlib import Path

    banned = ["成交金额", "成交客户数", "省份", "杭州", "借呗", "销售工号"]
    for relative in [
        "app/services/report_editorial_context.py",
        "app/services/report_limitation_attach.py",
        "app/services/report_reportability.py",
    ]:
        source = Path(relative).read_text(encoding="utf-8")
        for token in banned:
            assert token not in source
