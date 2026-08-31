from __future__ import annotations

import json

import pytest

from app.agent.orchestrator import AnalysisOrchestrator
from app.core.errors import ReportPipelineError
from app.llm.mock import MockLLMProvider
from app.models import AnalysisRun, Artifact
from app.schemas.actions import AgentActionResponse
from app.services.artifacts import ArtifactService
from app.services.metric_contract import MetricDefinition
from app.services.report_inputs import ReportInputCollector
from app.services.report_metric_fidelity import eligible_visual_contexts
from app.services.report_ready_artifacts import ReportReadyArtifact, validate_report_ready_artifacts
from app.services.reports import ReportService
from app.services.workspace import PathResolver
from app.skills.loader import SkillLoader
from tests.test_orchestrator import EXECUTE, FakeExecutor, prepare_run


def _metric() -> dict:
    return {
        "metric_id": "metric_x",
        "metric_scope": "reusable_measure",
        "label": "Metric X",
        "value": 0.42,
        "aggregation": "mean",
        "semantic_type": "rate",
        "unit_family": "percentage",
        "ratio_basis": "fraction",
        "definition": "Mean Metric X over the declared categories",
        "source_artifact": "data/category_comparison.csv",
        "source_field": "metric_x",
        "source_selector": {"category_a": "A"},
    }


def _finding() -> dict:
    return {
        "id": "finding_metric_x",
        "title": "Category A leads Metric X",
        "evidence": ["Category A records Metric X at 42%"],
        "risk": "The measured categories differ",
        "recommendation": "Monitor the category comparison",
        "related_artifacts": ["data/category_comparison.csv"],
        "claims": [
            {
                "claim_id": "claim_metric_x",
                "statement": "Category A records Metric X at 42%",
                "priority": "primary",
                "evidence_metric_ids": ["metric_x"],
                "evidence_artifact_paths": ["data/category_comparison.csv"],
            }
        ],
    }


def _binding(
    metric_ref: str | None = "metric_x",
    *,
    artifact_path: str = "data/category_comparison.csv",
    dimension_name: str = "category_a",
    measure_name: str = "metric_x",
) -> dict:
    measure = {
        "name": measure_name,
        "role": "measure",
        "presentation_usable": True,
    }
    if metric_ref is not None:
        measure["metric_ref"] = metric_ref
    return {
        "artifact_path": artifact_path,
        "fields": [
            {"name": dimension_name, "role": "dimension"},
            measure,
        ],
    }


def _complete(binding: dict) -> object:
    return AgentActionResponse.model_validate(
        {
            "action": "complete_analysis",
            "summary": "Metric X differs by category",
            "metrics": [_metric()],
            "report_ready_artifacts": [binding],
            "findings": [_finding()],
        }
    ).root


def _prepare_analysis_run(client, settings, name: str) -> tuple[str, str, PathResolver]:
    run_id = prepare_run(client, name)
    resolver = PathResolver(settings.workspace_root)
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        project_id = run.project_id
        target = resolver.resolve(project_id, "data/category_comparison.csv")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("category_a,metric_x\nA,0.42\nB,0.21\n", encoding="utf-8")
        ArtifactService(session).register(
            project_id, "data/category_comparison.csv", target.stat().st_size
        )
    return run_id, project_id, resolver


def _persist_creation_time_contract(client, project_id: str, resolver: PathResolver) -> None:
    metric = MetricDefinition.model_validate(_metric())
    metrics_target = resolver.resolve(project_id, "analysis/metrics.json")
    metrics_target.write_text(
        json.dumps(
            {"schema_version": "1.0", "metrics": [metric.model_dump(mode="json")]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    declaration = ReportReadyArtifact.model_validate(_binding())
    with client.app.state.database.session() as session:
        artifacts = ArtifactService(session)
        artifacts.register(project_id, "analysis/metrics.json", metrics_target.stat().st_size)
        artifacts.upsert_report_schemas(project_id, [declaration])


def test_complete_analysis_references_but_does_not_mutate_creation_time_contract(
    client, settings
) -> None:
    run_id, project_id, resolver = _prepare_analysis_run(
        client, settings, "Completion ownership"
    )
    _persist_creation_time_contract(client, project_id, resolver)
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    before_metrics = resolver.resolve(project_id, "analysis/metrics.json").read_text(
        encoding="utf-8"
    )
    with client.app.state.database.session() as session:
        artifact = session.query(Artifact).filter_by(
            project_id=project_id, path="data/category_comparison.csv"
        ).one()
        before_schema = artifact.report_schema_json
    action = AgentActionResponse.model_validate(
        {
            "action": "complete_analysis",
            "summary": "Metric X differs by category",
            "findings": [_finding()],
            "scalar_metrics": [],
            "referenced_metric_ids": ["metric_x"],
            "referenced_artifact_paths": ["data/category_comparison.csv"],
        }
    ).root

    assert orchestrator._complete_analysis(run_id, action)

    after_metrics = resolver.resolve(project_id, "analysis/metrics.json").read_text(
        encoding="utf-8"
    )
    with client.app.state.database.session() as session:
        artifact = session.query(Artifact).filter_by(
            project_id=project_id, path="data/category_comparison.csv"
        ).one()
        after_schema = artifact.report_schema_json
    assert json.loads(after_metrics)["metrics"][0]["metric_scope"] == "reusable_measure"
    assert json.loads(after_metrics)["metrics"][0]["source_field"] == "metric_x"
    assert before_metrics == after_metrics
    assert before_schema == after_schema


def test_complete_analysis_cannot_create_reusable_metric_or_report_schema(
    client, settings
) -> None:
    run_id, project_id, resolver = _prepare_analysis_run(
        client, settings, "Forbidden completion ownership"
    )
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )

    assert orchestrator._complete_analysis(run_id, _complete(_binding()))

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        artifact = session.query(Artifact).filter_by(
            project_id=project_id, path="data/category_comparison.csv"
        ).one()
        rejection = next(
            json.loads(event.data_json)
            for event in reversed(run.events)
            if event.event_type == "analysis.action_rejected"
        )
    assert rejection["reason"] == "complete_analysis_reusable_metric_forbidden"
    assert rejection["issues"][0]["code"] == "COMPLETE_ANALYSIS_REUSABLE_METRIC_OWNERSHIP"
    assert artifact.report_schema_json is None
    assert not resolver.resolve(project_id, "analysis/metrics.json").exists()


def test_complete_analysis_persists_measure_binding_on_existing_artifact(client, settings) -> None:
    run_id, project_id, resolver = _prepare_analysis_run(client, settings, "Measure binding")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )

    assert orchestrator._complete_analysis(run_id, _complete(_binding()))

    metrics_payload = json.loads(
        resolver.resolve(project_id, "analysis/metrics.json").read_text(encoding="utf-8")
    )
    assert set(metrics_payload) == {"schema_version", "metrics"}
    assert metrics_payload["metrics"][0]["metric_id"] == "metric_x"
    assert metrics_payload["metrics"][0]["metric_scope"] == "reusable_measure"
    assert "report_ready_artifacts" not in metrics_payload
    with client.app.state.database.session() as session:
        artifact = (
            session.query(Artifact)
            .filter_by(project_id=project_id, path="data/category_comparison.csv")
            .one()
        )
        assert json.loads(artifact.report_schema_json)["fields"][1]["metric_ref"] == "metric_x"
        inputs = ReportInputCollector(session, resolver).collect(
            project_id, "Analyze", "Neutral metric report"
        )
    columns = {
        item["name"]: item
        for item in next(
            entry for entry in inputs.catalog if entry.path == "data/category_comparison.csv"
        ).structure["columns"]
    }
    assert columns["category_a"]["role"] == "dimension"
    assert columns["metric_x"]["role"] == "measure"
    assert columns["metric_x"]["metric_ref"] == "metric_x"
    assert {item["visual_type"] for item in eligible_visual_contexts(inputs)} == {
        "chart",
        "table",
    }


@pytest.mark.parametrize(
    ("metric_ref", "expected_code"),
    [
        (None, "REPORT_READY_MEASURE_UNBOUND"),
        ("unknown_metric", "REPORT_READY_MEASURE_METRIC_UNKNOWN"),
    ],
)
def test_complete_analysis_rejects_invalid_measure_binding(
    client, settings, metric_ref, expected_code
) -> None:
    run_id, project_id, resolver = _prepare_analysis_run(
        client, settings, f"Invalid binding {expected_code}"
    )
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )

    assert orchestrator._complete_analysis(run_id, _complete(_binding(metric_ref)))

    assert not resolver.resolve(project_id, "analysis/metrics.json").exists()
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        rejection = next(
            json.loads(event.data_json)
            for event in reversed(run.events)
            if event.event_type == "analysis.action_rejected"
        )
    assert run.state == "ANALYZE"
    assert rejection["reason"] == "report_ready_artifact_invalid"
    assert rejection["issues"][0]["code"] == expected_code


def test_multi_row_measure_rejects_scalar_evidence_then_accepts_reusable_measure(
    client, settings
) -> None:
    run_id, project_id, resolver = _prepare_analysis_run(
        client, settings, "Neutral reusable measure scope"
    )
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    scalar_payload = _complete(_binding()).model_dump(mode="json")
    scalar_payload["metrics"][0]["metric_scope"] = "scalar_evidence"
    scalar = AgentActionResponse.model_validate(scalar_payload).root

    assert orchestrator._complete_analysis(run_id, scalar)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        rejection = next(
            json.loads(event.data_json)
            for event in reversed(run.events)
            if event.event_type == "analysis.action_rejected"
        )
    assert len(rejection["issues"]) == 1
    issue = rejection["issues"][0]
    assert {
        key: issue[key]
        for key in (
            "code",
            "artifact_path",
            "field",
            "metric_ref",
            "metric_scope",
            "required_metric_scope",
        )
    } == {
        "code": "REPORT_READY_MEASURE_NOT_REUSABLE",
        "artifact_path": "data/category_comparison.csv",
        "field": "metric_x",
        "metric_ref": "metric_x",
        "metric_scope": "scalar_evidence",
        "required_metric_scope": "reusable_measure",
    }
    assert issue["eligible_measures"] == []
    assert issue["physical_measure_fields"] == ["metric_x"]
    assert not resolver.resolve(project_id, "analysis/metrics.json").exists()

    repaired = _complete(_binding())
    assert orchestrator._complete_analysis(run_id, repaired)
    metrics = json.loads(
        resolver.resolve(project_id, "analysis/metrics.json").read_text(encoding="utf-8")
    )["metrics"]
    assert metrics[0]["metric_scope"] == "reusable_measure"


def test_complete_analysis_rejects_scalar_value_not_reproduced_by_declared_artifact(
    client, settings
) -> None:
    run_id, _, _ = _prepare_analysis_run(client, settings, "Scalar provenance mismatch")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    payload = _complete(_binding()).model_dump(mode="json")
    payload["metrics"][0]["metric_scope"] = "scalar_evidence"
    payload["metrics"][0]["value"] = 0.84
    invalid = AgentActionResponse.model_validate(payload).root

    assert orchestrator._complete_analysis(run_id, invalid)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        rejection = next(
            json.loads(event.data_json)
            for event in reversed(run.events)
            if event.event_type == "analysis.action_rejected"
        )
    assert rejection["reason"] == "metric_provenance_invalid"
    assert rejection["validation_stage"] == "metric_provenance_verification"
    assert rejection["issues"][0]["code"] == "METRIC_PROVENANCE_VALUE_MISMATCH"
    assert rejection["issues"][0]["reproduced_value"] == 0.42


def test_later_report_ready_candidate_replaces_earlier_metric_baseline(client, settings) -> None:
    run_id, project_id, resolver = _prepare_analysis_run(
        client, settings, "Stage-aware Sales-style repair"
    )
    target = resolver.resolve(project_id, "data/category_comparison.csv")
    target.write_text(
        "category_a,metric_x,metric_y,metric_z\nA,0.42,10,100\nB,0.21,20,200\n",
        encoding="utf-8",
    )
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    base_metric = {**_metric(), "metric_scope": "scalar_evidence"}
    metric_y = {
        **base_metric,
        "source_field": "metric_y",
        "metric_id": "metric_y",
        "label": "Metric Y",
        "value": 15,
        "unit_family": "count",
        "unit": "items",
        "semantic_type": "count",
        "aggregation": "sum",
        "count_semantics": "field_sum",
        "is_distinct": False,
    }
    metric_y.pop("ratio_basis")
    metric_z = {
        **metric_y,
        "metric_id": "metric_z",
        "source_field": "metric_z",
        "label": "Metric Z",
        "value": 150,
    }
    fields = [
        {"name": "category_a", "role": "dimension"},
        *[
            {
                "name": metric_id,
                "role": "measure",
                "metric_ref": metric_id,
                "presentation_usable": True,
            }
            for metric_id in ("metric_x", "metric_y", "metric_z")
        ],
    ]
    candidate_payload = _complete(_binding()).model_dump(mode="json")
    candidate_payload["metrics"] = [base_metric, dict(base_metric), metric_y, metric_z]
    candidate_payload["report_ready_artifacts"][0]["fields"] = fields
    metric_invalid = AgentActionResponse.model_validate(candidate_payload).root

    assert orchestrator._complete_analysis(run_id, metric_invalid)

    candidate_payload["metrics"] = [base_metric, metric_y, metric_z]
    report_ready_invalid = AgentActionResponse.model_validate(candidate_payload).root
    assert orchestrator._complete_analysis(run_id, report_ready_invalid)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        state = json.loads(run.complete_analysis_repair_state_json)
        context = orchestrator._context(session, run)
    assert state["best_validation_stage"] == "report_ready_artifacts"
    assert state["best_issue_count"] == 3
    assert {issue["code"] for issue in state["best_issues"]} == {
        "REPORT_READY_MEASURE_NOT_REUSABLE"
    }
    feedback = next(
        message["content"]
        for message in context
        if message["content"].startswith("<pending_complete_analysis_candidate")
    )
    assert feedback.count("REPORT_READY_MEASURE_NOT_REUSABLE") == 3
    assert "METRIC_REGISTRY_INVALID" not in feedback

    accepted_payload = report_ready_invalid.model_dump(mode="json")
    for metric in accepted_payload["metrics"]:
        metric["metric_scope"] = "reusable_measure"
    accepted = AgentActionResponse.model_validate(accepted_payload).root
    assert orchestrator._complete_analysis(run_id, accepted)
    metrics = json.loads(
        resolver.resolve(project_id, "analysis/metrics.json").read_text(encoding="utf-8")
    )["metrics"]
    assert {metric["metric_scope"] for metric in metrics} == {"reusable_measure"}


def test_field_typo_repair_uses_previous_candidate_and_real_available_fields(
    client, settings
) -> None:
    run_id, project_id, resolver = _prepare_analysis_run(
        client, settings, "Field typo candidate repair"
    )
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    typo = _complete(_binding(measure_name="metric_xx"))

    assert orchestrator._complete_analysis(run_id, typo)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        context = orchestrator._context(session, run)
        rejection = next(
            json.loads(event.data_json)
            for event in reversed(run.events)
            if event.event_type == "analysis.action_rejected"
        )
        assert run.complete_analysis_repair_state_json is not None
    issue = next(
        item for item in rejection["issues"] if item["code"] == "REPORT_READY_FIELD_UNKNOWN"
    )
    assert issue["field"] == "metric_xx"
    assert issue["available_fields"] == ["category_a", "metric_x"]
    feedback = "\n".join(message["content"] for message in context)
    assert '<pending_complete_analysis_candidate status="INVALID"' in feedback
    assert '"name":"metric_xx"' in feedback
    assert '"available_fields":["category_a","metric_x"]' in feedback
    assert "Do not regenerate valid declarations" in feedback
    assert "execute Python" in feedback
    assert not resolver.resolve(project_id, "analysis/metrics.json").exists()

    corrected = _complete(_binding()).model_copy(update={"summary": "Regenerated summary"})
    assert orchestrator._complete_analysis(run_id, corrected)

    findings = json.loads(
        resolver.resolve(project_id, "analysis/findings.json").read_text(encoding="utf-8")
    )
    assert findings["summary"] == "Metric X differs by category"
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        assert run.complete_analysis_repair_state_json is None
        restored = [
            event
            for event in run.events
            if event.event_type == "analysis.complete_analysis_repair_locked_content_restored"
        ]
    assert restored


def test_repair_deterministically_preserves_valid_artifact_declarations(client, settings) -> None:
    run_id, project_id, resolver = _prepare_analysis_run(
        client, settings, "Preserve valid declarations"
    )
    secondary = resolver.resolve(project_id, "data/secondary_comparison.csv")
    secondary.write_text("category_b,metric_x\nA,0.31\nB,0.22\n", encoding="utf-8")
    with client.app.state.database.session() as session:
        ArtifactService(session).register(
            project_id, "data/secondary_comparison.csv", secondary.stat().st_size
        )
    first_payload = _complete(_binding()).model_dump(mode="json")
    first_payload["report_ready_artifacts"].append(
        _binding(
            artifact_path="data/secondary_comparison.csv",
            dimension_name="category_typo",
        )
    )
    first = AgentActionResponse.model_validate(first_payload).root
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )

    assert orchestrator._complete_analysis(run_id, first)

    repair_payload = _complete(
        _binding(
            artifact_path="data/secondary_comparison.csv",
            dimension_name="category_b",
        )
    ).model_dump(mode="json")
    repair_payload["report_ready_artifacts"] = [repair_payload["report_ready_artifacts"][0]]
    repair = AgentActionResponse.model_validate(repair_payload).root
    assert orchestrator._complete_analysis(run_id, repair)

    with client.app.state.database.session() as session:
        artifacts = {
            artifact.path: json.loads(artifact.report_schema_json)
            for artifact in session.query(Artifact).filter(Artifact.project_id == project_id).all()
            if artifact.report_schema_json
        }
        run = session.get(AnalysisRun, run_id)
        restored = [
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.complete_analysis_repair_locked_content_restored"
        ]
    assert set(artifacts) == {
        "data/category_comparison.csv",
        "data/secondary_comparison.csv",
    }
    assert artifacts["data/category_comparison.csv"]["fields"][0]["name"] == "category_a"
    assert artifacts["data/secondary_comparison.csv"]["fields"][0]["name"] == ("category_b")
    assert "valid_report_ready_artifacts" in restored[-1]["fields"]


def test_current_fulfillment_schema_regression_returns_compact_real_fields(
    client, settings
) -> None:
    run_id, project_id, resolver = _prepare_analysis_run(
        client, settings, "Fulfillment schema regression"
    )
    artifact_path = "data/olist_fulfillment_monthly.csv"
    target = resolver.resolve(project_id, artifact_path)
    target.write_text(
        "purchase_month,delivered_orders,avg_delivery_days,late_orders,late_rate\n"
        "2018-01,100,12.4,8,0.08\n",
        encoding="utf-8",
    )
    with client.app.state.database.session() as session:
        ArtifactService(session).register(project_id, artifact_path, target.stat().st_size)
    candidate = _complete(
        _binding(
            artifact_path=artifact_path,
            dimension_name="year_month",
            measure_name="avg_delivery_days",
        )
    )
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )

    assert orchestrator._complete_analysis(run_id, candidate)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        rejection = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.action_rejected"
        )
        context = orchestrator._context(session, run)
    issue = rejection["issues"][0]
    assert issue["code"] == "REPORT_READY_FIELD_UNKNOWN"
    assert issue["field"] == "year_month"
    assert issue["available_fields"] == [
        "purchase_month",
        "delivered_orders",
        "avg_delivery_days",
        "late_orders",
        "late_rate",
    ]
    repair_context = "\n".join(message["content"] for message in context)
    assert '"purchase_month"' in repair_context
    assert "stdout" not in repair_context


def test_report_ready_rejects_incompatible_reusable_metric_source(client, settings) -> None:
    run_id, project_id, resolver = _prepare_analysis_run(client, settings, "Source closure")
    bad_metric = _metric()
    bad_metric["source_field"] = "other_metric"
    declaration = _binding()
    issues = validate_report_ready_artifacts(
        resolver,
        project_id,
        [ReportReadyArtifact.model_validate(declaration)],
        [MetricDefinition.model_validate(bad_metric)],
    )
    issue = next(item for item in issues if item["code"] == "REPORT_READY_MEASURE_INCOMPATIBLE")
    assert issue["mismatch"] == "source_field"
    assert issue["metric_source_field"] == "other_metric"
    assert issue["required_source_field"] == "metric_x"
    assert issue["eligible_measures"] == []


@pytest.mark.asyncio
async def test_non_tabular_declaration_repair_rejects_python_without_execution(
    client, settings
) -> None:
    run_id, project_id, resolver = _prepare_analysis_run(
        client, settings, "Non-tabular declaration repair"
    )
    target = resolver.resolve(project_id, "data/summary.json")
    target.write_text('{"metric_x":0.42}', encoding="utf-8")
    with client.app.state.database.session() as session:
        ArtifactService(session).register(project_id, "data/summary.json", target.stat().st_size)
    payload = _complete(_binding()).model_dump(mode="json")
    payload["report_ready_artifacts"] = [
        {
            "artifact_path": "data/summary.json",
            "fields": [
                {"name": "category_a", "role": "dimension"},
                {
                    "name": "metric_x",
                    "role": "measure",
                    "metric_ref": "metric_x",
                },
            ],
        }
    ]
    invalid = AgentActionResponse.model_validate(payload).root
    executor = FakeExecutor()
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), executor
    )

    assert orchestrator._complete_analysis(run_id, invalid)
    execute = AgentActionResponse.model_validate(EXECUTE).root
    assert await orchestrator._execute(run_id, execute)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        rejection = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.action_rejected"
            and json.loads(event.data_json).get("reason") == "report_ready_artifact_invalid"
        )
        python_rejection = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.action_rejected"
            and json.loads(event.data_json).get("reason")
            == "report_ready_declaration_requires_complete_analysis"
        )
    issue = rejection["issues"][0]
    assert issue == {
        "code": "REPORT_READY_ARTIFACT_NOT_TABULAR",
        "artifact_path": "data/summary.json",
        "artifact_kind": "json/object",
        "eligible_for_tabular_visual": False,
    }
    assert python_rejection["action"] == "execute_python"
    assert run.execution_count == 0
    assert executor.statuses == ["success"]


@pytest.mark.asyncio
async def test_explicit_missing_artifact_issue_may_execute_python(client, settings) -> None:
    run_id, _, _ = _prepare_analysis_run(client, settings, "Missing artifact preparation")
    payload = _complete(_binding()).model_dump(mode="json")
    payload["report_ready_artifacts"][0]["artifact_path"] = "data/missing_output.csv"
    invalid = AgentActionResponse.model_validate(payload).root
    executor = FakeExecutor(["success"])
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), executor
    )

    assert orchestrator._complete_analysis(run_id, invalid)
    execute = AgentActionResponse.model_validate(EXECUTE).root
    assert await orchestrator._execute(run_id, execute)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
    assert run.execution_count == 1
    assert executor.statuses == []


def test_report_ready_repair_tracks_progress_and_keeps_best_candidate(client, settings) -> None:
    settings.max_report_preparation_attempts = 2
    run_id, _, _ = _prepare_analysis_run(client, settings, "Best candidate repair")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    first = _complete(_binding(dimension_name="category_bad", measure_name="metric_bad"))
    second = _complete(_binding(measure_name="metric_bad"))
    third = _complete(
        _binding(dimension_name="category_regressed", measure_name="metric_regressed")
    )

    assert orchestrator._complete_analysis(run_id, first)
    assert orchestrator._complete_analysis(run_id, second)
    assert orchestrator._complete_analysis(run_id, third)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        state = json.loads(run.complete_analysis_repair_state_json)
        context = orchestrator._context(session, run)
        transitions = [
            json.loads(event.data_json)["repair_transition"]
            for event in run.events
            if event.event_type == "analysis.action_rejected"
            and json.loads(event.data_json).get("reason") == "report_ready_artifact_invalid"
        ]
    assert [item["classification"] for item in transitions] == [
        "initial",
        "progressing",
        "regressed",
    ]
    assert state["best_issue_count"] == 1
    assert state["best_candidate"]["report_ready_artifacts"][0]["fields"][1]["name"] == (
        "metric_bad"
    )
    assert state["best_issues"][0]["field"] == "metric_bad"
    candidate_feedback = next(
        message["content"]
        for message in context
        if message["content"].startswith("<pending_complete_analysis_candidate")
    )
    assert '"name":"metric_bad"' in candidate_feedback
    assert "metric_regressed" not in candidate_feedback
    assert run.status != "failed"


def test_identical_candidate_and_issues_stop_as_repeated_declaration(client, settings) -> None:
    settings.max_report_preparation_attempts = 1
    run_id, _, _ = _prepare_analysis_run(client, settings, "Repeated declaration")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    invalid = _complete(_binding(measure_name="metric_bad"))

    assert orchestrator._complete_analysis(run_id, invalid)
    assert not orchestrator._complete_analysis(run_id, invalid)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        stopped = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.report_repair_stopped"
        )
    assert stopped["mode"] == "report_ready_declaration_repeated"
    assert run.error_message == (
        "Report-ready artifact repair stopped because repeated attempts produced the same "
        "invalid declaration"
    )
    assert run.complete_analysis_repair_state_json is None


def test_progress_then_identical_locked_result_uses_repeated_error_message(
    client, settings
) -> None:
    settings.max_report_preparation_attempts = 2
    run_id, _, _ = _prepare_analysis_run(client, settings, "Repair regression limit")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    first = _complete(_binding(dimension_name="category_bad", measure_name="metric_bad"))
    best = _complete(_binding(measure_name="metric_bad"))
    regressed = _complete(
        _binding(dimension_name="category_regressed", measure_name="metric_regressed")
    )

    assert orchestrator._complete_analysis(run_id, first)
    assert orchestrator._complete_analysis(run_id, best)
    assert orchestrator._complete_analysis(run_id, regressed)
    assert orchestrator._complete_analysis(run_id, regressed)
    assert not orchestrator._complete_analysis(run_id, regressed)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        stopped = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.report_repair_stopped"
        )
    assert stopped["mode"] == "report_ready_declaration_repeated"
    assert stopped["best_issue_count"] == 1
    assert run.error_message == (
        "Report-ready artifact repair stopped because repeated attempts produced the same "
        "invalid declaration"
    )


def _editor_spec(*, chart: bool, bad_field: bool = False) -> dict:
    blocks: list[dict] = [
        {
            "type": "narrative",
            "text": "Metric X differs across the verified categories.",
            "claim_ids": ["claim_metric_x"],
            "purpose": "Introduce the comparison",
        }
    ]
    role = "narrative_led"
    if chart:
        role = "chart_led"
        blocks.extend(
            [
                {
                    "type": "chart",
                    "data_ref": "data/category_comparison.csv",
                    "chart_type": "bar",
                    "x_field": "category_a",
                    "series": ["missing_metric_x" if bad_field else "metric_x"],
                    "title": "Metric X by category",
                    "purpose": "Compare the registered measure across categories",
                },
                {
                    "type": "narrative",
                    "text": "Category A is higher on the registered measure.",
                    "claim_ids": ["claim_metric_x"],
                    "purpose": "Interpret the comparison",
                    "display_role": "evidence_interpretation",
                    "related_block_id": "data/category_comparison.csv",
                    "metric_refs": ["metric_x"],
                },
            ]
        )
    return {
        "headline": "Metric X differs across categories",
        "summary": "The verified comparison shows Category A at 42%.",
        "kpis": [],
        "sections": [
            {
                "title": "Category A leads the registered measure",
                "finding_refs": ["finding_metric_x"],
                "claim_ids": ["claim_metric_x"],
                "section_role": role,
                "layout": "flow",
                "blocks": blocks,
            }
        ],
    }


def _prepare_report_project(client, settings) -> tuple[str, PathResolver]:
    run_id, project_id, resolver = _prepare_analysis_run(client, settings, "Neutral report")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    assert orchestrator._complete_analysis(run_id, _complete(_binding()))
    return project_id, resolver


async def _generate(client, settings, project_id, resolver, provider):
    with client.app.state.database.session() as session:
        return await ReportService(
            session, resolver, SkillLoader(settings.skill_root), provider
        ).generate(project_id, "Analyze Metric X", "Neutral metric report")


@pytest.mark.asyncio
async def test_business_retry_receives_previous_draft_and_preserves_a_valid_visual(
    client, settings
) -> None:
    project_id, resolver = _prepare_report_project(client, settings)
    provider = MockLLMProvider([_editor_spec(chart=True, bad_field=True), _editor_spec(chart=True)])

    path = await _generate(client, settings, project_id, resolver, provider)

    retry_payload = json.loads(provider.requests[1][-1]["content"])
    assert retry_payload["previous_draft"]["sections"][0]["blocks"][1]["type"] == "chart"
    assert "Never delete all analytical visuals" in retry_payload["repair"]
    spec = json.loads(
        resolver.resolve(project_id, "reports/report_spec.json").read_text(encoding="utf-8")
    )
    chart = next(
        block["chart"]
        for section in spec["sections"]
        for block in section["blocks"]
        if block["type"] == "chart"
    )
    assert chart["series"][0]["metric"] == "metric_x"
    assert chart["series"][0]["format"] == "percent"
    assert chart["series"][0]["scale"] == pytest.approx(0.01)
    assert resolver.resolve(project_id, path).is_file()


@pytest.mark.asyncio
async def test_anti_evasion_gate_rejects_narrative_only_llm_report(client, settings) -> None:
    project_id, resolver = _prepare_report_project(client, settings)

    with pytest.raises(ReportPipelineError) as exc_info:
        await _generate(
            client,
            settings,
            project_id,
            resolver,
            MockLLMProvider([_editor_spec(chart=False)]),
        )

    assert exc_info.value.code == "analytical_visuals_dropped"
    assert exc_info.value.details["issue"] == "ANALYTICAL_VISUALS_DROPPED"
    assert exc_info.value.details["eligible_visual_count"] == 2
    assert not resolver.resolve(project_id, "reports/report_spec.json").exists()


def _dataset_regression_action(
    *,
    artifact_path: str,
    dimension: str,
    measure: str,
    reusable_metric_id: str,
    scalar_metric_id: str,
    scalar_value: float = 200,
    semantic_type: str = "measure",
    unit_family: str = "currency",
) -> object:
    return AgentActionResponse.model_validate(
        {
            "action": "complete_analysis",
            "summary": "The verified multi-row comparison is reportable",
            "metrics": [
                {
                    "metric_id": scalar_metric_id,
                    "metric_scope": "scalar_evidence",
                    "label": "Period B observation",
                    "value": scalar_value,
                    "aggregation": "sum",
                    "semantic_type": semantic_type,
                    "unit_family": unit_family,
                    "definition": "Verified Period B observation",
                    "source_artifact": artifact_path,
                    "source_field": measure,
                    "source_selector": {dimension: "B"},
                    "grain": "dimension_row",
                },
                {
                    "metric_id": reusable_metric_id,
                    "metric_scope": "reusable_measure",
                    "label": measure,
                    "value": 300,
                    "aggregation": "sum",
                    "semantic_type": semantic_type,
                    "unit_family": unit_family,
                    "definition": f"Sum of {measure} across the declared dimension",
                    "source_artifact": artifact_path,
                    "grain": "dimension_row",
                },
            ],
            "findings": [
                {
                    "id": "finding_dataset_regression",
                    "title": "Period B records the verified observation",
                    "evidence": ["Period B records 200"],
                    "risk": "The comparison requires continued monitoring",
                    "recommendation": "Monitor the verified comparison",
                    "related_artifacts": [artifact_path],
                    "claims": [
                        {
                            "claim_id": "claim_dataset_regression",
                            "statement": "Period B records 200",
                            "evidence_metric_ids": [scalar_metric_id],
                            "evidence_artifact_paths": [artifact_path],
                        }
                    ],
                }
            ],
            "report_ready_artifacts": [
                {
                    "artifact_path": artifact_path,
                    "fields": [
                        {"name": dimension, "role": "dimension"},
                        {
                            "name": measure,
                            "role": "measure",
                            "metric_ref": reusable_metric_id,
                        },
                    ],
                }
            ],
        }
    ).root


def _dataset_editor_spec(*, artifact_path: str, dimension: str, measure: str) -> dict:
    return {
        "headline": "The verified comparison is reportable",
        "summary": "The report preserves the multi-row analytical evidence.",
        "kpis": [],
        "sections": [
            {
                "title": "Period B records the verified observation",
                "finding_refs": ["finding_dataset_regression"],
                "claim_ids": ["claim_dataset_regression"],
                "section_role": "chart_led",
                "layout": "flow",
                "blocks": [
                    {
                        "type": "narrative",
                        "text": "Period B records the verified observation.",
                        "claim_ids": ["claim_dataset_regression"],
                        "purpose": "Introduce the result",
                    },
                    {
                        "type": "chart",
                        "data_ref": artifact_path,
                        "chart_type": "bar",
                        "x_field": dimension,
                        "series": [measure],
                        "title": "Verified comparison",
                        "purpose": "Compare the reusable measure",
                    },
                    {
                        "type": "narrative",
                        "text": "The dimension values differ on the reusable measure.",
                        "purpose": "Interpret the comparison",
                        "display_role": "evidence_interpretation",
                        "related_block_id": artifact_path,
                        "metric_refs": [measure],
                    },
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_sales_series_uses_reusable_amount_metric_not_period_scalar(client, settings) -> None:
    run_id = prepare_run(client, "Sales reusable measure regression")
    resolver = PathResolver(settings.workspace_root)
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        project_id = run.project_id
        artifact = resolver.resolve(project_id, "data/report_monthly_trend.csv")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("period,amount\nA,100\nB,200\n", encoding="utf-8")
        ArtifactService(session).register(
            project_id, "data/report_monthly_trend.csv", artifact.stat().st_size
        )
    action = _dataset_regression_action(
        artifact_path="data/report_monthly_trend.csv",
        dimension="period",
        measure="amount",
        reusable_metric_id="amount",
        scalar_metric_id="period_b_amount",
    )
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    assert orchestrator._complete_analysis(run_id, action)

    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver).collect(
            project_id, "Analyze sales", "Sales report"
        )
        path = await ReportService(
            session,
            resolver,
            SkillLoader(settings.skill_root),
            MockLLMProvider(
                [
                    _dataset_editor_spec(
                        artifact_path="data/report_monthly_trend.csv",
                        dimension="period",
                        measure="amount",
                    )
                ]
            ),
        ).generate(project_id, "Analyze sales", "Sales report")
    assert {item["visual_type"] for item in eligible_visual_contexts(inputs)} == {
        "chart",
        "table",
    }
    spec = json.loads(
        resolver.resolve(project_id, "reports/report_spec.json").read_text(encoding="utf-8")
    )
    chart = next(
        block["chart"]
        for section in spec["sections"]
        for block in section["blocks"]
        if block["type"] == "chart"
    )
    assert chart["series"][0]["metric"] == "amount"
    assert chart["series"][0]["metric"] != "period_b_amount"
    assert resolver.resolve(project_id, path).is_file()


@pytest.mark.asyncio
async def test_ecommerce_bound_fulfillment_measure_keeps_analytical_visual(
    client, settings
) -> None:
    run_id = prepare_run(client, "Ecommerce reusable measure regression")
    resolver = PathResolver(settings.workspace_root)
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        project_id = run.project_id
        artifact = resolver.resolve(project_id, "data/fulfillment_by_region.csv")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("region,delivery_duration\nA,3.1\nB,4.2\n", encoding="utf-8")
        ArtifactService(session).register(
            project_id, "data/fulfillment_by_region.csv", artifact.stat().st_size
        )
    action = _dataset_regression_action(
        artifact_path="data/fulfillment_by_region.csv",
        dimension="region",
        measure="delivery_duration",
        reusable_metric_id="delivery_duration",
        scalar_metric_id="region_b_delivery_duration",
        scalar_value=4.2,
        semantic_type="duration",
        unit_family="duration",
    )
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    assert orchestrator._complete_analysis(run_id, action)

    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver).collect(
            project_id, "Analyze fulfillment", "Fulfillment report"
        )
        path = await ReportService(
            session,
            resolver,
            SkillLoader(settings.skill_root),
            MockLLMProvider(
                [
                    _dataset_editor_spec(
                        artifact_path="data/fulfillment_by_region.csv",
                        dimension="region",
                        measure="delivery_duration",
                    )
                ]
            ),
        ).generate(project_id, "Analyze fulfillment", "Fulfillment report")
    assert {item["visual_type"] for item in eligible_visual_contexts(inputs)} == {
        "chart",
        "table",
    }
    spec = json.loads(
        resolver.resolve(project_id, "reports/report_spec.json").read_text(encoding="utf-8")
    )
    assert (
        sum(block["type"] == "chart" for section in spec["sections"] for block in section["blocks"])
        == 1
    )
    assert resolver.resolve(project_id, path).is_file()
