from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent.orchestrator import AnalysisOrchestrator
from app.llm.mock import MockLLMProvider
from app.models import AnalysisRun, Artifact
from app.schemas.actions import AgentActionResponse
from app.schemas.execution import ExecutionResult
from app.services.report_inputs import ReportInputCollector
from app.services.report_metric_fidelity import eligible_visual_contexts
from app.services.workspace import PathResolver
from tests.test_orchestrator import prepare_run


class ContractExecutor:
    async def execute(self, workspace: Path, script_path: str, execution_id: str):
        (workspace / "data" / "category_metrics.csv").write_text(
            "category_a,metric_x\nA,1\nB,3\n",
            encoding="utf-8",
        )
        return ExecutionResult(
            execution_id=execution_id,
            status="success",
            exit_code=0,
            stdout="done",
            stderr="",
            duration_ms=1,
            script_path=script_path,
        )


def execute_action():
    return AgentActionResponse.model_validate(
        {
            "action": "execute_python",
            "task_id": "task_category",
            "filename": "category.py",
            "code": "print('done')",
            "purpose": "Create neutral category metrics",
            "expected_artifacts": ["data/category_metrics.csv"],
            "artifact_contracts": [
                {
                    "artifact_path": "data/category_metrics.csv",
                    "origin_task_id": "task_category",
                    "grain": "category_a",
                    "fields": [
                        {"name": "category_a", "role": "dimension"},
                        {
                            "name": "metric_x",
                            "role": "measure",
                            "metric_ref": "metric_x",
                        },
                    ],
                    "metrics": [
                        {
                            "metric_id": "metric_x",
                            "metric_scope": "reusable_measure",
                            "label": "Metric X",
                            "value": 3,
                            "aggregation": "sum",
                            "semantic_type": "measure",
                            "unit_family": "currency",
                            "unit": "USD",
                            "grain": "category_a",
                            "definition": "Sum of metric_x by category_a",
                            "source_artifact": "data/category_metrics.csv",
                            "source_field": "metric_x",
                            "source_selector": {"category_a": "B"},
                        }
                    ],
                }
            ],
        }
    ).root


def complete_action():
    return AgentActionResponse.model_validate(
        {
            "action": "complete_analysis",
            "summary": "Metric X differs by category",
            "findings": [
                {
                    "id": "finding_metric_x",
                    "title": "Category B has the larger Metric X",
                    "evidence": ["Category B records Metric X at 3"],
                    "risk": "The comparison covers two categories",
                    "recommendation": "Continue monitoring the category comparison",
                    "related_artifacts": ["data/category_metrics.csv"],
                    "claims": [
                        {
                            "claim_id": "claim_metric_x",
                            "statement": "Category B records Metric X at 3",
                            "priority": "primary",
                            "evidence_metric_ids": ["metric_x"],
                            "evidence_artifact_paths": ["data/category_metrics.csv"],
                        }
                    ],
                }
            ],
            "scalar_metrics": [],
            "referenced_metric_ids": ["metric_x"],
            "referenced_artifact_paths": ["data/category_metrics.csv"],
        }
    ).root


def contract_snapshot(client, resolver: PathResolver, project_id: str) -> dict:
    metrics = json.loads(
        resolver.resolve(project_id, "analysis/metrics.json").read_text(encoding="utf-8")
    )["metrics"]
    with client.app.state.database.session() as session:
        schemas = {
            artifact.path: json.loads(artifact.report_schema_json)
            for artifact in session.scalars(
                select(Artifact).where(
                    Artifact.project_id == project_id,
                    Artifact.report_schema_json.is_not(None),
                )
            )
        }
    return {
        "reusable_metrics": {
            metric["metric_id"]: metric
            for metric in metrics
            if metric["metric_scope"] == "reusable_measure"
        },
        "report_schemas": schemas,
    }


@pytest.mark.asyncio
async def test_neutral_contract_closes_before_completion_and_remains_unchanged(
    client, settings
) -> None:
    run_id = prepare_run(client, "Neutral creation lifecycle")
    resolver = PathResolver(settings.workspace_root)
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        MockLLMProvider([]),
        ContractExecutor(),
    )

    assert await orchestrator._execute(run_id, execute_action())

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        project_id = run.project_id
        execution = next(
            json.loads(event.data_json)
            for event in reversed(run.events)
            if event.event_type == "analysis.execution_completed"
        )
    before_completion = contract_snapshot(client, resolver, project_id)
    assert execution["registered_report_schemas"] == ["data/category_metrics.csv"]
    assert execution["registered_reusable_metrics"] == ["metric_x"]
    assert set(before_completion["reusable_metrics"]) == {"metric_x"}
    assert set(before_completion["report_schemas"]) == {"data/category_metrics.csv"}

    assert orchestrator._complete_analysis(run_id, complete_action())

    after_completion = contract_snapshot(client, resolver, project_id)
    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver).collect(
            project_id, "Analyze neutral categories", "Neutral report"
        )
    assert after_completion == before_completion
    assert {item["visual_type"] for item in eligible_visual_contexts(inputs)} == {
        "chart",
        "table",
    }
