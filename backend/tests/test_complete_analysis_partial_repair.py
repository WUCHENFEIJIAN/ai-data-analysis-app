from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.agent.orchestrator import AnalysisOrchestrator
from app.llm.mock import MockLLMProvider
from app.models import AnalysisRun, Artifact
from app.schemas.actions import CompleteAnalysisAction, CompleteAnalysisRepairResult
from app.services.artifacts import ArtifactService
from app.services.complete_analysis_repair import (
    apply_partial_repair_result,
    build_partial_repair_context,
    load_repair_baseline,
    supports_partial_repair,
)
from app.services.metric_contract import MetricDefinition
from app.services.workspace import PathResolver
from tests.test_orchestrator import FakeExecutor, prepare_run


def scalar_metric(metric_id: str = "metric_snapshot", value: float = 1) -> dict:
    return {
        "metric_id": metric_id,
        "metric_scope": "scalar_evidence",
        "label": "Metric snapshot",
        "value": value,
        "aggregation": "sum",
        "semantic_type": "measure",
        "unit_family": "currency",
        "unit": "USD",
        "definition": "Verified scalar snapshot",
        "source_artifact": "data/summary.json",
        "source_field": "metric_snapshot",
    }


def completion_candidate() -> CompleteAnalysisAction:
    return CompleteAnalysisAction.model_validate(
        {
            "action": "complete_analysis",
            "summary": "Verified summary",
            "findings": [
                {
                    "id": "finding_1",
                    "title": "Verified finding",
                    "evidence": ["The verified snapshot is available"],
                    "risk": "The observation is scoped",
                    "recommendation": "Continue monitoring",
                    "related_artifacts": ["data/summary.json"],
                    "claims": [],
                }
            ],
            "scalar_metrics": [scalar_metric()],
            "referenced_artifact_paths": ["data/summary.json"],
        }
    )


def test_partial_scalar_metric_repair_preserves_unrelated_completion_content() -> None:
    baseline = completion_candidate()
    replacement = scalar_metric(value=2) | {"label": "Corrected snapshot"}
    result = CompleteAnalysisRepairResult.model_validate(
        {"repair_type": "metric", "metric_replacements": [replacement]}
    )

    merged, changed = apply_partial_repair_result(
        baseline,
        result,
        [{"code": "METRIC_REGISTRY_INVALID", "metric_id": "metric_snapshot"}],
    )

    assert changed == ["scalar_metrics"]
    assert merged.scalar_metrics[0].value == 2
    assert merged.summary == baseline.summary
    assert merged.findings == baseline.findings
    assert merged.report_ready_artifacts == []


def test_partial_repair_schema_disables_reusable_and_report_ready_repair() -> None:
    reusable = scalar_metric() | {
        "metric_scope": "reusable_measure",
        "source_artifact": "data/table.csv",
        "source_field": "metric_x",
        "grain": "category_a",
    }
    with pytest.raises(ValidationError, match="only scalar_evidence"):
        CompleteAnalysisRepairResult.model_validate(
            {"repair_type": "metric", "metric_replacements": [reusable]}
        )
    with pytest.raises(ValidationError):
        CompleteAnalysisRepairResult.model_validate(
            {
                "repair_type": "report_ready",
                "report_ready_artifact_replacements": [
                    {"artifact_path": "data/table.csv", "remove_artifact": True}
                ],
            }
        )
    properties = CompleteAnalysisRepairResult.model_json_schema()["properties"]
    assert "report_ready_artifact_replacements" not in properties


@pytest.mark.parametrize(
    "code",
    [
        "REPORT_READY_MEASURE_METRIC_UNKNOWN",
        "REPORT_READY_REUSABLE_METRIC_MISSING",
        "REPORT_READY_MEASURE_NOT_REUSABLE",
    ],
)
def test_reusable_and_report_ready_contract_issues_are_not_llm_repairable(code: str) -> None:
    assert not supports_partial_repair([{"code": code, "artifact_path": "data/table.csv"}])


def test_scalar_claim_provenance_issue_remains_repairable() -> None:
    assert supports_partial_repair(
        [
            {
                "code": "METRIC_PROVENANCE_VALUE_MISMATCH",
                "metric_id": "metric_snapshot",
                "finding_id": "finding_1",
                "claim_id": "claim_1",
            }
        ]
    )


def test_corrupted_creation_time_contract_fails_fast_without_repair_state(client, settings) -> None:
    run_id = prepare_run(client, "Corrupt creation contract")
    resolver = PathResolver(settings.workspace_root)
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        project_id = run.project_id
        target = resolver.resolve(project_id, "data/table.csv")
        target.write_text("category_a,metric_x\nA,1\nB,2\n", encoding="utf-8")
        artifacts = ArtifactService(session)
        artifact = artifacts.register(project_id, "data/table.csv", target.stat().st_size)
        artifact.report_schema_json = json.dumps(
            {
                "artifact_path": "data/table.csv",
                "origin_task_id": "task_1",
                "grain": "category_a",
                "fields": [
                    {"name": "category_a", "role": "dimension"},
                    {
                        "name": "metric_x",
                        "role": "measure",
                        "metric_ref": "missing_metric",
                    },
                ],
            }
        )
        metrics_target = resolver.resolve(project_id, "analysis/metrics.json")
        metric = MetricDefinition.model_validate(
            {
                **scalar_metric("metric_x"),
                "metric_scope": "reusable_measure",
                "source_artifact": "data/table.csv",
                "source_field": "metric_x",
                "grain": "category_a",
            }
        )
        metrics_target.write_text(
            json.dumps({"schema_version": "1.0", "metrics": [metric.model_dump(mode="json")]}),
            encoding="utf-8",
        )
        artifacts.register(project_id, "analysis/metrics.json", metrics_target.stat().st_size)
    action = CompleteAnalysisAction.model_validate(
        {
            "action": "complete_analysis",
            "summary": "Verified summary",
            "findings": [
                {
                    "id": "finding_1",
                    "title": "Verified finding",
                    "evidence": ["Verified business evidence"],
                    "risk": "Scoped observation",
                    "recommendation": "Continue monitoring",
                    "related_artifacts": ["data/table.csv"],
                    "claims": [],
                }
            ],
            "referenced_artifact_paths": ["data/table.csv"],
        }
    )
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        MockLLMProvider([]),
        FakeExecutor(),
    )

    assert not orchestrator._complete_analysis(run_id, action)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        artifact = (
            session.query(Artifact).filter_by(project_id=project_id, path="data/table.csv").one()
        )
        failed = next(
            json.loads(event.data_json)
            for event in reversed(run.events)
            if event.event_type == "analysis.failed"
        )
    assert run.status == "failed"
    assert run.complete_analysis_repair_state_json is None
    assert failed["error_code"] == "REPORT_READY_CONTRACT_INVALID"
    assert "missing_metric" in artifact.report_schema_json


def test_claim_repair_requires_one_consistent_evidence_representation() -> None:
    with pytest.raises(ValidationError, match="must change an evidence field"):
        CompleteAnalysisRepairResult.model_validate(
            {
                "repair_type": "provenance",
                "claim_replacements": [{"finding_id": "finding_1", "claim_id": "claim_1"}],
            }
        )
    with pytest.raises(ValidationError, match="flattened evidence fields or evidence_groups"):
        CompleteAnalysisRepairResult.model_validate(
            {
                "repair_type": "provenance",
                "claim_replacements": [
                    {
                        "finding_id": "finding_1",
                        "claim_id": "claim_1",
                        "evidence_metric_ids": ["metric_a"],
                        "evidence_groups": [{"metric_ids": ["metric_b"]}],
                    }
                ],
            }
        )


def test_claim_repair_rejects_empty_metric_list_as_a_noop() -> None:
    with pytest.raises(ValidationError, match="evidence_metric_ids must be non-empty"):
        CompleteAnalysisRepairResult.model_validate(
            {
                "repair_type": "provenance",
                "claim_replacements": [
                    {
                        "finding_id": "finding_1",
                        "claim_id": "claim_1",
                        "evidence_metric_ids": [],
                    }
                ],
            }
        )


def test_partial_provenance_repair_can_add_scalar_metric_and_normalizes_group() -> None:
    baseline = CompleteAnalysisAction.model_construct(
        action="complete_analysis",
        summary="Verified summary",
        findings=[
            {
                "id": "finding_1",
                "title": "Team coverage",
                "evidence": ["A team coverage result"],
                "risk": "The result is scoped",
                "recommendation": "Monitor coverage",
                "claims": [
                    {
                        "claim_id": "claim_1",
                        "statement": "Team coverage is 80%",
                        "evidence_metric_ids": [],
                        "evidence_artifact_paths": [],
                        "evidence_groups": [],
                    }
                ],
            }
        ],
        scalar_metrics=[],
        metrics=[],
        referenced_metric_ids=[],
        referenced_artifact_paths=[],
        report_ready_artifacts=[],
    )
    metric = scalar_metric("team_coverage", 0.8) | {
        "label": "Team coverage",
        "semantic_type": "rate",
        "unit_family": "percentage",
        "ratio_value_basis": "fraction",
        "unit": "%",
        "definition": "Verified team coverage",
        "source_artifact": "data/summary.json",
        "source_field": "team_coverage",
    }
    result = CompleteAnalysisRepairResult.model_validate(
        {
            "repair_type": "provenance",
            "metric_replacements": [metric],
            "claim_replacements": [
                {
                    "finding_id": "finding_1",
                    "claim_id": "claim_1",
                    "evidence_groups": [
                        {
                            "metric_ids": ["team_coverage"],
                            "artifact_paths": ["data/summary.json"],
                        }
                    ],
                }
            ],
        }
    )

    merged, changed = apply_partial_repair_result(
        baseline,
        result,
        [
            {
                "code": "FINDING_METRIC_PROVENANCE_MISSING",
                "finding_id": "finding_1",
                "claim_id": "claim_1",
            }
        ],
    )

    claim = merged.findings[0].claims[0]
    assert changed == ["findings", "scalar_metrics"]
    assert merged.scalar_metrics[0].metric_id == "team_coverage"
    assert claim.evidence_metric_ids == ["team_coverage"]
    assert claim.evidence_artifact_paths == ["data/summary.json"]


def test_partial_repair_context_exposes_available_metrics() -> None:
    context = build_partial_repair_context(
        {"findings": [], "scalar_metrics": [], "metrics": []},
        [{"code": "FINDING_METRIC_PROVENANCE_MISSING"}],
        validation_stage="metric_provenance_declaration",
        available_metrics=[
            {
                "metric_id": "team_count",
                "metric_scope": "reusable_measure",
                "source_artifact": "data/team_summary.csv",
                "source_field": "team",
            }
        ],
    )

    assert context["available_metrics"][0]["metric_id"] == "team_count"


def test_partial_repair_rejects_a_candidate_with_no_effective_change() -> None:
    baseline = completion_candidate()
    result = CompleteAnalysisRepairResult.model_validate(
        {
            "repair_type": "provenance",
            "claim_replacements": [
                {
                    "finding_id": "finding_1",
                    "claim_id": "claim_1",
                    "evidence_metric_ids": ["metric_snapshot"],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="no effective candidate change"):
        apply_partial_repair_result(
            baseline,
            result,
            [
                {
                    "code": "FINDING_METRIC_UNREGISTERED",
                    "finding_id": "finding_1",
                    "claim_id": "claim_1",
                }
            ],
        )


def test_load_repair_baseline_accepts_legacy_invalid_quantitative_claim() -> None:
    baseline = load_repair_baseline(
        {
            "action": "complete_analysis",
            "summary": "Legacy candidate",
            "findings": [
                {
                    "id": "finding_legacy",
                    "title": "Legacy finding",
                    "evidence": ["A result"],
                    "risk": "Scoped",
                    "recommendation": "Review",
                    "claims": [
                        {
                            "claim_id": "claim_legacy",
                            "statement": "Coverage is 80%",
                            "evidence_metric_ids": [],
                        }
                    ],
                }
            ],
        }
    )

    assert baseline.findings[0]["claims"][0]["statement"] == "Coverage is 80%"
