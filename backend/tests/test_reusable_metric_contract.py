from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.execution import ExecutionResult
from app.services.execution import PythonExecutionService
from app.services.metric_contract import MetricDefinition, MetricValidationError, MetricValidator
from app.services.report_ready_artifacts import (
    AnalysisArtifactContract,
    ScalarArtifactContract,
    validate_report_ready_artifacts,
)
from app.services.workspace import PathResolver, WorkspaceService


def _base_metric(metric_id: str, *, scope: str = "reusable_measure", **overrides):
    payload = {
        "metric_id": metric_id,
        "metric_scope": scope,
        "label": metric_id,
        "value": None if scope == "reusable_measure" else 100,
        "aggregation": "sum",
        "semantic_type": "measure",
        "unit_family": "currency",
        "unit": "USD",
        "grain": "category",
        "definition": f"Sum of {metric_id}",
        "source_artifact": "data/summary.csv",
        "source_field": metric_id,
    }
    payload.update(overrides)
    return payload


def _grouped_contract(path: str = "data/summary.csv", grain: str = "category", *, avg_value=None):
    metrics = [
        _base_metric("amount", source_artifact=path, source_field="amount", grain=grain),
        _base_metric(
            "count",
            source_artifact=path,
            source_field="count",
            grain=grain,
            aggregation="count",
            semantic_type="count",
            unit_family="count",
            unit="items",
            count_semantics="row_count",
            is_distinct=False,
        ),
        _base_metric(
            "avg",
            source_artifact=path,
            source_field="avg",
            grain=grain,
            value=avg_value,
            aggregation="ratio",
            semantic_type="ratio",
            numerator="amount",
            denominator="count",
            ratio_basis="per_row",
            definition="amount / count",
        ),
    ]
    return AnalysisArtifactContract.model_validate(
        {
            "artifact_path": path,
            "origin_task_id": "task_summary",
            "grain": grain,
            "fields": [
                {"name": "category", "role": "dimension"},
                {"name": "amount", "role": "measure", "metric_ref": "amount"},
                {"name": "count", "role": "measure", "metric_ref": "count"},
                {"name": "avg", "role": "measure", "metric_ref": "avg"},
            ],
            "metrics": metrics,
        }
    )


def test_reusable_measure_omits_scalar_value() -> None:
    metric = MetricDefinition.model_validate(
        _base_metric("total_amount", source_field="total_amount")
    )

    assert metric.value is None
    MetricValidator.validate([metric])


def test_reusable_ratio_validates_binding_without_metric_values() -> None:
    contract = _grouped_contract()

    assert all(metric.value is None for metric in contract.metrics)
    MetricValidator.validate(contract.metrics)


def test_grouped_rate_is_reusable_at_artifact_grain() -> None:
    metrics = [
        _base_metric(
            "orders",
            source_artifact="data/monthly.csv",
            source_field="orders",
            grain="month",
            aggregation="count",
            semantic_type="count",
            unit_family="count",
            unit="orders",
            count_semantics="event_count",
            is_distinct=False,
        ),
        _base_metric(
            "on_time_orders",
            source_artifact="data/monthly.csv",
            source_field="on_time_orders",
            grain="month",
            aggregation="count",
            semantic_type="count",
            unit_family="count",
            unit="orders",
            count_semantics="event_count",
            is_distinct=False,
        ),
        _base_metric(
            "on_time_rate",
            source_artifact="data/monthly.csv",
            source_field="on_time_rate",
            grain="month",
            aggregation="ratio",
            semantic_type="rate",
            unit_family="percentage",
            unit="%",
            numerator="on_time_orders",
            denominator="orders",
            ratio_basis="other",
            ratio_value_basis="fraction",
            definition="on_time_orders / orders",
        ),
    ]
    contract = AnalysisArtifactContract.model_validate(
        {
            "artifact_path": "data/monthly.csv",
            "origin_task_id": "task_monthly",
            "grain": "month",
            "fields": [
                {"name": "month", "role": "dimension"},
                {"name": "orders", "role": "measure", "metric_ref": "orders"},
                {
                    "name": "on_time_orders",
                    "role": "measure",
                    "metric_ref": "on_time_orders",
                },
                {
                    "name": "on_time_rate",
                    "role": "measure",
                    "metric_ref": "on_time_rate",
                },
            ],
            "metrics": metrics,
        }
    )

    assert contract.grain == "month"
    assert contract.metrics[2].metric_scope == "reusable_measure"
    assert contract.metrics[2].grain == "month"
    assert contract.metrics[2].value is None


def test_grouped_rate_missing_grain_is_rejected() -> None:
    metrics = [
        _base_metric(
            "orders",
            source_artifact="data/monthly.csv",
            source_field="orders",
            grain="month",
            aggregation="count",
            semantic_type="count",
            unit_family="count",
            unit="orders",
            count_semantics="event_count",
            is_distinct=False,
        ),
        _base_metric(
            "on_time_orders",
            source_artifact="data/monthly.csv",
            source_field="on_time_orders",
            grain="month",
            aggregation="count",
            semantic_type="count",
            unit_family="count",
            unit="orders",
            count_semantics="event_count",
            is_distinct=False,
        ),
        _base_metric(
            "on_time_rate",
            source_artifact="data/monthly.csv",
            source_field="on_time_rate",
            grain=None,
            aggregation="ratio",
            semantic_type="rate",
            unit_family="percentage",
            unit="%",
            numerator="on_time_orders",
            denominator="orders",
            ratio_basis="other",
            ratio_value_basis="fraction",
            definition="on_time_orders / orders",
        ),
    ]

    with pytest.raises(ValueError, match="grain None.*month"):
        AnalysisArtifactContract.model_validate(
            {
                "artifact_path": "data/monthly.csv",
                "origin_task_id": "task_monthly",
                "grain": "month",
                "fields": [
                    {"name": "month", "role": "dimension"},
                    {"name": "orders", "role": "measure", "metric_ref": "orders"},
                    {
                        "name": "on_time_orders",
                        "role": "measure",
                        "metric_ref": "on_time_orders",
                    },
                    {
                        "name": "on_time_rate",
                        "role": "measure",
                        "metric_ref": "on_time_rate",
                    },
                ],
                "metrics": metrics,
            }
        )



def test_overall_scalar_rate_is_allowed_in_scalar_artifact_contract() -> None:
    contract = ScalarArtifactContract.model_validate(
        {
            "artifact_path": "data/overall.json",
            "metrics": [
                {
                    "metric_id": "overall_on_time_rate",
                    "metric_scope": "scalar_evidence",
                    "label": "Overall on-time rate",
                    "value": 0.8,
                    "aggregation": "rate",
                    "semantic_type": "rate",
                    "unit_family": "percentage",
                    "unit": "%",
                    "definition": "Materialized overall on-time rate",
                    "source_artifact": "data/overall.json",
                    "source_field": "overall_on_time_rate",
                }
            ],
        }
    )

    assert contract.metrics[0].metric_scope == "scalar_evidence"
    assert contract.metrics[0].value == 0.8


def test_grouped_rate_cannot_be_moved_to_scalar_contract_without_value() -> None:
    with pytest.raises(ValueError, match="scalar_evidence metrics require a materialized value"):
        ScalarArtifactContract.model_validate(
            {
                "artifact_path": "data/monthly.csv",
                "metrics": [
                    {
                        "metric_id": "on_time_rate",
                        "metric_scope": "scalar_evidence",
                        "label": "Monthly on-time rate",
                        "value": None,
                        "aggregation": "ratio",
                        "semantic_type": "rate",
                        "unit_family": "percentage",
                        "unit": "%",
                        "numerator": "on_time_orders",
                        "denominator": "orders",
                        "ratio_basis": "other",
                        "ratio_value_basis": "fraction",
                        "definition": "on_time_orders / orders",
                        "source_artifact": "data/monthly.csv",
                        "source_field": "on_time_rate",
                    }
                ],
            }
        )


def test_reusable_ratio_rejects_missing_source_binding() -> None:
    payload = _grouped_contract().model_dump(mode="json")
    payload["metrics"][2]["source_field"] = "missing_avg"

    with pytest.raises(ValueError, match="source_field"):
        AnalysisArtifactContract.model_validate(payload)


def test_report_ready_allows_null_ratio_when_denominator_is_zero(tmp_path: Path) -> None:
    project_id = "pj_" + "a" * 32
    WorkspaceService(tmp_path).create(project_id)
    resolver = PathResolver(tmp_path)
    target = resolver.resolve(project_id, "data/summary.csv")
    target.write_text("category,amount,count,avg\nA,100,10,10\nB,200,0,\n", encoding="utf-8")
    contract = _grouped_contract()

    issues = validate_report_ready_artifacts(
        resolver, project_id, [contract.report_ready_declaration()], contract.metrics
    )

    assert not any(
        issue["code"] == "REPORT_READY_RATIO_ZERO_DENOMINATOR_VALUE" for issue in issues
    )
    # The existing coverage policy may reject a partially-null presentation series, but it
    # must not reinterpret the null ratio as numeric zero.


def test_report_ready_rejects_numeric_ratio_when_denominator_is_zero(tmp_path: Path) -> None:
    project_id = "pj_" + "b" * 32
    WorkspaceService(tmp_path).create(project_id)
    resolver = PathResolver(tmp_path)
    target = resolver.resolve(project_id, "data/summary.csv")
    target.write_text("category,amount,count,avg\nA,100,10,10\nB,200,0,0\n", encoding="utf-8")
    contract = _grouped_contract(avg_value=None)

    issues = validate_report_ready_artifacts(
        resolver, project_id, [contract.report_ready_declaration()], contract.metrics
    )

    assert any(issue["code"] == "REPORT_READY_RATIO_ZERO_DENOMINATOR_VALUE" for issue in issues)


def test_scalar_ratio_still_requires_materialized_value_and_checks_zero_denominator() -> None:
    scalar = MetricDefinition(
        metric_id="scalar_avg",
        metric_scope="scalar_evidence",
        label="Scalar average",
        value=0,
        aggregation="ratio",
        semantic_type="ratio",
        unit_family="currency",
        numerator="total_amount",
        denominator="total_count",
        ratio_basis="per_row",
        definition="total_amount / total_count",
        source_artifact="data/summary.json",
    )
    amount = MetricDefinition(
        metric_id="total_amount",
        metric_scope="scalar_evidence",
        label="Total amount",
        value=100,
        aggregation="sum",
        semantic_type="measure",
        unit_family="currency",
        definition="Sum of amount",
        source_artifact="data/summary.json",
    )
    count = MetricDefinition(
        metric_id="total_count",
        metric_scope="scalar_evidence",
        label="Total count",
        value=0,
        aggregation="count",
        semantic_type="count",
        unit_family="count",
        count_semantics="row_count",
        is_distinct=False,
        definition="Count of rows",
        source_artifact="data/summary.json",
    )

    with pytest.raises(MetricValidationError, match="denominator must not be zero"):
        MetricValidator.validate([amount, count, scalar])


def test_field_sum_denominator_does_not_allow_per_entity_for_reusable_ratio() -> None:
    total = MetricDefinition.model_validate(
        _base_metric("amount", source_field="amount")
    )
    count = MetricDefinition.model_validate(
        _base_metric(
            "customers",
            source_field="customers",
            aggregation="sum",
            semantic_type="count",
            unit_family="count",
            unit="people",
            count_semantics="field_sum",
            is_distinct=False,
        )
    )
    ratio = MetricDefinition.model_validate(
        _base_metric(
            "avg_customer_amount",
            source_field="avg",
            aggregation="ratio",
            semantic_type="ratio",
            numerator="amount",
            denominator="customers",
            ratio_basis="per_entity",
            definition="amount / customers",
        )
    )

    with pytest.raises(MetricValidationError, match="per_entity ratio"):
        MetricValidator.validate([total, count, ratio])


def test_field_sum_measure_to_measure_ratio_uses_existing_other_basis() -> None:
    total = MetricDefinition.model_validate(_base_metric("amount", source_field="amount"))
    count = MetricDefinition.model_validate(
        _base_metric(
            "customers",
            source_field="customers",
            aggregation="sum",
            semantic_type="count",
            unit_family="count",
            unit="people",
            count_semantics="field_sum",
            is_distinct=False,
        )
    )
    ratio = MetricDefinition.model_validate(
        _base_metric(
            "avg_customer_amount",
            source_field="avg",
            aggregation="ratio",
            semantic_type="ratio",
            numerator="amount",
            denominator="customers",
            ratio_basis="other",
            definition="amount / customers",
        )
    )

    MetricValidator.validate([total, count, ratio])


def _batch_contract(path: str, grain: str) -> AnalysisArtifactContract:
    prefix = grain.replace("-", "_")
    metrics = [
        _base_metric(f"{prefix}_amount", source_artifact=path, source_field="amount", grain=grain),
        _base_metric(
            f"{prefix}_count",
            source_artifact=path,
            source_field="count",
            grain=grain,
            aggregation="count",
            semantic_type="count",
            unit_family="count",
            unit="items",
            count_semantics="row_count",
            is_distinct=False,
        ),
        _base_metric(
            f"{prefix}_avg",
            source_artifact=path,
            source_field="avg",
            grain=grain,
            aggregation="ratio",
            semantic_type="ratio",
            numerator=f"{prefix}_amount",
            denominator=f"{prefix}_count",
            ratio_basis="per_row",
            definition="amount / count",
        ),
    ]
    return AnalysisArtifactContract.model_validate(
        {
            "artifact_path": path,
            "origin_task_id": "task_batch",
            "grain": grain,
            "fields": [
                {"name": "category", "role": "dimension"},
                {"name": "amount", "role": "measure", "metric_ref": f"{prefix}_amount"},
                {"name": "count", "role": "measure", "metric_ref": f"{prefix}_count"},
                {"name": "avg", "role": "measure", "metric_ref": f"{prefix}_avg"},
            ],
            "metrics": metrics,
        }
    )


@pytest.mark.asyncio
async def test_grouped_contract_batch_registers_all_contracts_atomically(client, settings) -> None:
    project = client.post("/api/projects", json={"name": "Grouped contract batch"}).json()
    paths = [
        ("data/day.csv", "day"),
        ("data/product.csv", "product"),
        ("data/region.csv", "region"),
        ("data/role.csv", "role"),
    ]

    class FakeExecutor:
        async def execute(self, workspace_path, script_path, execution_id):
            for relative_path, _ in paths:
                target = workspace_path / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    "category,amount,count,avg\nA,100,10,10\nB,200,20,10\n",
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

    contracts = [_batch_contract(path, grain) for path, grain in paths]
    resolver = PathResolver(settings.workspace_root)
    with client.app.state.database.session() as session:
        result = await PythonExecutionService(
            session, resolver, FakeExecutor()
        ).execute(
            project["id"],
            "grouped.py",
            "print('done')",
            expected_artifacts=[path for path, _ in paths],
            artifact_contracts=contracts,
        )

    assert result.status == "success"
    assert result.artifact_contract_issues == []
    assert result.registered_report_schemas == [path for path, _ in paths]
    assert len(result.registered_reusable_metrics) == 12
    metrics = json.loads(
        resolver.resolve(project["id"], "analysis/metrics.json").read_text(encoding="utf-8")
    )["metrics"]
    assert len([item for item in metrics if item["metric_scope"] == "reusable_measure"]) == 12
    assert all(item.get("value") is None for item in metrics)

