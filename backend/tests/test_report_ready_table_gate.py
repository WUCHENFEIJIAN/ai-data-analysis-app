from __future__ import annotations

import json

import pytest

from app.llm.mock import MockLLMProvider
from app.services.artifacts import ArtifactService
from app.services.report_editor_prompt import ReportEditorPromptLoader
from app.services.report_editor_spec import ReportEditorSpec
from app.services.report_editorial_context import EditorialContextBuilder
from app.services.report_inputs import ReportInputCollector
from app.services.report_metric_fidelity import (
    build_visual_context,
    eligible_visual_contexts,
)
from app.services.report_pipeline_diagnostics import input_counts
from app.services.report_validator import ReportSpecValidator
from tests.test_report_editor_pipeline import (
    _editor_spec,
    _generate,
    _metric,
    prepare_editor_project,
)


def _inputs(client, settings, project, resolver):
    with client.app.state.database.session() as session:
        return ReportInputCollector(session, resolver).collect(
            project["id"], "Analyze", "Neutral report-ready gate"
        )


def _table_draft() -> dict:
    draft = _editor_spec()
    draft["sections"][0]["section_role"] = "table_led"
    draft["sections"][0]["blocks"] = [
        draft["sections"][0]["blocks"][0],
        {
            "type": "table",
            "data_ref": "data/summary.csv",
            "columns": ["region", "sales"],
            "title": "Neutral comparison",
            "purpose": "Show exact analytical values",
        },
        {
            "type": "narrative",
            "text": "The declared values show a measurable category difference.",
            "purpose": "Interpret the analytical table",
            "display_role": "evidence_interpretation",
            "related_block_id": "data/summary.csv",
            "metric_refs": ["sales"],
        },
    ]
    return draft


@pytest.mark.asyncio
async def test_unaccepted_csv_is_not_a_table_context_or_final_analytical_table(
    client, settings
) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True)
    inputs = _inputs(client, settings, project, resolver)

    assert build_visual_context(inputs) == []
    assert eligible_visual_contexts(inputs) == []
    context = EditorialContextBuilder.build(inputs)
    assert context["visuals"] == []
    assert context["eligible_visuals"] == []

    result = ReportSpecValidator.validate(ReportEditorSpec.model_validate(_table_draft()), inputs)
    assert any(issue.code == "table.not_report_ready" for issue in result.issues)
    assert all(block.type != "table" for block in result.spec.sections[0].blocks)

    path = await _generate(
        client,
        settings,
        project,
        resolver,
        MockLLMProvider([_table_draft(), _editor_spec()]),
    )
    report = json.loads(
        resolver.resolve(project["id"], "reports/report_spec.json").read_text(encoding="utf-8")
    )
    assert path == "reports/report.html"
    assert (
        sum(
            block["type"] == "table"
            for section in report["sections"]
            for block in section["blocks"]
        )
        == 0
    )


def test_accepted_report_ready_csv_is_eligible_for_analytical_table(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True, report_ready=True)
    inputs = _inputs(client, settings, project, resolver)

    contexts = eligible_visual_contexts(inputs)
    table = next(
        item
        for item in contexts
        if item["data_ref"] == "data/summary.csv" and item["visual_type"] == "table"
    )
    assert table["report_ready"] is True
    assert table["dimension"] == "region"
    assert table["metric_refs"] == ["total_sales"]
    assert input_counts(inputs)["eligible_charts"] == 1
    assert input_counts(inputs)["eligible_tables"] == 1
    result = ReportSpecValidator.validate(ReportEditorSpec.model_validate(_table_draft()), inputs)
    assert not any(issue.code.startswith("table.") for issue in result.issues)
    assert any(block.type == "table" for block in result.spec.sections[0].blocks)


def test_scalar_json_metric_source_remains_available_but_not_visual_eligible(
    client, settings
) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    summary = resolver.resolve(project["id"], "data/summary_a.json")
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text('{"summary_value": 3}', encoding="utf-8")
    metrics_path = resolver.resolve(project["id"], "analysis/metrics.json")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["metrics"].append(_metric("summary_value", 3, "data/summary_a.json"))
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
    with client.app.state.database.session() as session:
        ArtifactService(session).register(
            project["id"], "data/summary_a.json", summary.stat().st_size
        )

    inputs = _inputs(client, settings, project, resolver)

    assert any(
        metric.metric_id == "summary_value" and metric.source_artifact == "data/summary_a.json"
        for metric in inputs.metrics
    )
    assert any(entry.path == "data/summary_a.json" for entry in inputs.catalog)
    assert all(item["data_ref"] != "data/summary_a.json" for item in build_visual_context(inputs))


def test_unbound_physical_column_is_not_valid_for_analytical_table(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True, report_ready=True)
    resolver.resolve(project["id"], "data/summary.csv").write_text(
        "region,sales,raw_value\nEast,100,9\nWest,40,8\n", encoding="utf-8"
    )
    inputs = _inputs(client, settings, project, resolver)
    draft = _table_draft()
    draft["sections"][0]["blocks"][1]["columns"] = ["region", "raw_value"]

    result = ReportSpecValidator.validate(ReportEditorSpec.model_validate(draft), inputs)

    assert any(issue.code == "table.field_not_report_ready" for issue in result.issues)
    assert all(block.type != "table" for block in result.spec.sections[0].blocks)


def test_report_editor_prompt_does_not_treat_catalog_as_visual_eligibility() -> None:
    prompt = ReportEditorPromptLoader().load()

    assert "Artifact-backed Analytical Chart / Table 只能从" in prompt
    assert "`artifact_catalog` 不能单独赋予 Artifact 分析型可视化资格" in prompt
    assert "当 `eligible_visuals=[]` 时" in prompt
