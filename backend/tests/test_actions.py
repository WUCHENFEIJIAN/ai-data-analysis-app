import pytest
from pydantic import ValidationError

from app.schemas.actions import AgentActionResponse


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "ask_user", "question": "Which metric?", "reason": "Ambiguous"},
        {
            "action": "create_plan",
            "title": "Plan",
            "objective": "Analyze",
            "tasks": [{"id": "task_1", "title": "Overview", "goal": "Summarize", "sequence": 1}],
        },
        {
            "action": "execute_python",
            "task_id": "task_1",
            "filename": "overview.py",
            "code": "print(1)",
            "purpose": "Calculate",
        },
        {
            "action": "complete_analysis",
            "metrics": [],
            "summary": "Done",
            "findings": [
                {
                    "id": "finding_1",
                    "title": "Finding",
                    "evidence": ["output.csv: value=1"],
                    "risk": "Low",
                    "recommendation": "Continue",
                    "related_artifacts": ["data/output.csv"],
                }
            ],
        },
        {
            "action": "declare_report_evidence",
            "schema_version": "1.0",
            "metrics": [],
            "kpis": [],
            "artifacts": [],
        },
        {"action": "generate_report", "title": "Report", "style": "FT"},
    ],
)
def test_all_agent_actions_are_strictly_validated(payload: dict[str, object]) -> None:
    assert AgentActionResponse.model_validate(payload).root.action == payload["action"]


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "shell", "command": "whoami"},
        {"action": "ask_user", "question": "Missing reason"},
        {"action": "generate_report", "title": "Report", "unknown": True},
    ],
)
def test_unknown_incomplete_and_extra_action_data_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentActionResponse.model_validate(payload)


def test_complete_analysis_schema_names_metric_identifier_canonically() -> None:
    schema = AgentActionResponse.model_json_schema()
    metric_properties = schema["$defs"]["MetricDefinition"]["properties"]
    metrics_schema = schema["$defs"]["CompleteAnalysisAction"]["properties"]["scalar_metrics"]

    assert "metric_id" in metric_properties
    assert "id" not in metric_properties
    assert "scalar_evidence" in metrics_schema["description"]
    assert "Reusable measures are already persisted" in metrics_schema["description"]
    artifact_schema = schema["$defs"]["ExecutePythonAction"]["properties"]["artifact_contracts"]
    assert "dimensional CSV/tabular outputs" in artifact_schema["description"]
    assert "ratio, rate, and percentage measure fields" in artifact_schema["description"]
    assert "grain must exactly equal the artifact grain" in artifact_schema["description"]
    scalar_schema = schema["$defs"]["ExecutePythonAction"]["properties"][
        "scalar_artifact_contracts"
    ]
    assert "overall_record_count" in scalar_schema["description"]
    assert "multi-row grouped measure" in scalar_schema["description"]


def test_execute_python_accepts_local_artifact_contract_owned_by_task() -> None:
    action = AgentActionResponse.model_validate(
        {
            "action": "execute_python",
            "task_id": "task_category",
            "filename": "category.py",
            "code": "print(1)",
            "purpose": "Create category metrics",
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
                            "presentation_usable": True,
                        },
                    ],
                    "metrics": [
                        {
                            "metric_id": "metric_x",
                            "metric_scope": "reusable_measure",
                            "label": "Metric X",
                            "value": 2,
                            "aggregation": "sum",
                            "semantic_type": "measure",
                            "unit_family": "currency",
                            "unit": "USD",
                            "grain": "category_a",
                            "definition": "Sum of metric_x by category_a",
                            "source_artifact": "data/category_metrics.csv",
                            "source_field": "metric_x",
                        }
                    ],
                }
            ],
        }
    ).root

    assert action.artifact_contracts[0].origin_task_id == action.task_id


def test_execute_python_deterministically_owns_contract_with_action_task() -> None:
    action = AgentActionResponse.model_validate(
        {
            "action": "execute_python",
            "task_id": "task_category",
            "filename": "category.py",
            "code": "print(1)",
            "purpose": "Create category metrics",
            "expected_artifacts": ["data/category_metrics.csv"],
            "artifact_contracts": [
                {
                    "artifact_path": "data/category_metrics.csv",
                    "origin_task_id": "task_other",
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
                            "value": 2,
                            "aggregation": "sum",
                            "semantic_type": "measure",
                            "unit_family": "currency",
                            "grain": "category_a",
                            "definition": "Sum of metric_x by category_a",
                            "source_artifact": "data/category_metrics.csv",
                            "source_field": "metric_x",
                        }
                    ],
                }
            ],
        }
    ).root

    assert action.artifact_contracts[0].origin_task_id == "task_category"


def test_execute_python_accepts_scalar_artifact_contract() -> None:
    action = AgentActionResponse.model_validate(
        {
            "action": "execute_python",
            "task_id": "task_summary",
            "filename": "summary.py",
            "code": "print(1)",
            "purpose": "Create scalar summary",
            "expected_artifacts": ["data/summary.json"],
            "scalar_artifact_contracts": [
                {
                    "artifact_path": "data/summary.json",
                    "metrics": [
                        {
                            "metric_id": "total_amount",
                            "metric_scope": "scalar_evidence",
                            "label": "Total amount",
                            "value": 7,
                            "aggregation": "sum",
                            "semantic_type": "measure",
                            "unit_family": "currency",
                            "definition": "Sum of the verified amount",
                            "source_artifact": "data/summary.json",
                            "source_field": "total_amount",
                        }
                    ],
                }
            ],
        }
    ).root

    assert action.scalar_artifact_contracts[0].metrics[0].metric_scope == "scalar_evidence"


def test_scalar_artifact_contract_rejects_missing_source_field() -> None:
    with pytest.raises(ValidationError, match="must declare source_field"):
        AgentActionResponse.model_validate(
            {
                "action": "execute_python",
                "task_id": "task_summary",
                "filename": "summary.py",
                "code": "print(1)",
                "purpose": "Create scalar summary",
                "expected_artifacts": ["data/summary.json"],
                "scalar_artifact_contracts": [
                    {
                        "artifact_path": "data/summary.json",
                        "metrics": [
                            {
                                "metric_id": "total_amount",
                                "label": "Total amount",
                                "value": 7,
                                "aggregation": "sum",
                                "semantic_type": "measure",
                                "unit_family": "currency",
                                "definition": "Sum of the verified amount",
                                "source_artifact": "data/summary.json",
                            }
                        ],
                    }
                ],
            }
        )


def test_dimension_contract_rejects_scalar_metric_grain_and_explains_scalar_route() -> None:
    with pytest.raises(ValidationError, match="scalar_artifact_contracts"):
        AgentActionResponse.model_validate(
            {
                "action": "execute_python",
                "task_id": "task_summary",
                "filename": "summary.py",
                "code": "print(1)",
                "purpose": "Create a summary table",
                "expected_artifacts": ["data/overall_summary.csv"],
                "artifact_contracts": [
                    {
                        "artifact_path": "data/overall_summary.csv",
                        "grain": "dataset",
                        "fields": [
                            {"name": "label", "role": "dimension"},
                            {
                                "name": "overall_record_count",
                                "role": "measure",
                                "metric_ref": "overall_record_count",
                            },
                        ],
                        "metrics": [
                            {
                                "metric_id": "overall_record_count",
                                "metric_scope": "reusable_measure",
                                "label": "Overall record count",
                                "value": 10,
                                "aggregation": "count",
                                "semantic_type": "count",
                                "unit_family": "count",
                                "count_semantics": "row_count",
                                "is_distinct": False,
                                "grain": "overall",
                                "definition": "Count all records",
                                "source_artifact": "data/overall_summary.csv",
                                "source_field": "overall_record_count",
                            }
                        ],
                    }
                ],
            }
        )
