import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Artifact
from app.schemas.actions import AgentActionResponse
from app.schemas.execution import ExecutionResult
from app.services.artifacts import ArtifactDetector, ArtifactService
from app.services.execution import PythonExecutionService
from app.services.report_inputs import ReportInputCollector
from app.services.report_ready_artifacts import AnalysisArtifactContract, ScalarArtifactContract
from app.services.scripts import ScriptManager
from app.services.workspace import PathResolver, WorkspaceService


def workspace(tmp_path: Path) -> tuple[str, PathResolver]:
    project_id = "pj_" + "c" * 32
    WorkspaceService(tmp_path).create(project_id)
    return project_id, PathResolver(tmp_path)


def test_script_manager_numbers_sanitizes_and_never_overwrites(tmp_path: Path) -> None:
    project_id, resolver = workspace(tmp_path)
    manager = ScriptManager(resolver)

    first = manager.save(project_id, "../../Overview Report.py", "print(1)")
    second = manager.save(project_id, "Overview Report.py", "print(2)")
    fixed = manager.save(project_id, "overview_fix.py", "print(3)")

    assert first == "scripts/001_overview_report.py"
    assert second == "scripts/002_overview_report.py"
    assert fixed == "scripts/003_overview_fix.py"
    assert resolver.resolve(project_id, first).read_text() == "print(1)"


def test_artifact_detector_finds_new_and_modified_outputs(tmp_path: Path) -> None:
    project_id, resolver = workspace(tmp_path)
    root = resolver.project_root(project_id)
    existing = resolver.resolve(project_id, "data/result.csv")
    existing.write_text("a\n1\n")
    before = ArtifactDetector().snapshot(root)
    existing.write_text("a\n1\n2\n")
    resolver.resolve(project_id, "charts/chart.png").write_bytes(b"png")
    resolver.resolve(project_id, "logs/noise.log").write_text("ignored")
    resolver.resolve(project_id, "input/source.csv").write_text("ignored")

    changed = ArtifactDetector.changed(before, ArtifactDetector().snapshot(root))

    assert changed == ["charts/chart.png", "data/result.csv"]


@pytest.mark.asyncio
async def test_execution_service_saves_script_and_registers_detected_artifacts(
    client, settings
) -> None:
    project = client.post("/api/projects", json={"name": "Execution"}).json()

    class FakeExecutor:
        async def execute(self, workspace_path, script_path, execution_id):
            (workspace_path / "data" / "result.csv").write_text("value\n1\n")
            return ExecutionResult(
                execution_id=execution_id,
                status="success",
                exit_code=0,
                stdout="done",
                stderr="",
                duration_ms=1,
                script_path=script_path,
            )

    database = client.app.state.database
    with database.session() as session:
        result = await PythonExecutionService(
            session,
            PathResolver(settings.workspace_root),
            FakeExecutor(),
        ).execute(project["id"], "overview.py", "print('done')")
        artifacts = list(
            session.scalars(select(Artifact).where(Artifact.project_id == project["id"]))
        )

    assert result.new_artifacts == ["data/result.csv", "scripts/001_overview.py"]
    assert {artifact.path for artifact in artifacts} == set(result.new_artifacts)


@pytest.mark.asyncio
async def test_json_literal_preflight_skips_docker_execution(client, settings) -> None:
    project = client.post("/api/projects", json={"name": "Preflight"}).json()

    class CountingExecutor:
        calls = 0

        async def execute(self, workspace_path, script_path, execution_id):
            self.calls += 1
            raise AssertionError("Docker must not run when Python preflight fails")

    executor = CountingExecutor()
    with client.app.state.database.session() as session:
        result = await PythonExecutionService(
            session, PathResolver(settings.workspace_root), executor
        ).execute(project["id"], "invalid.py", 'config = {"enabled": false}')

    assert executor.calls == 0
    assert result.status == "failed"
    assert result.docker_executed is False
    assert result.failure["source"] == "preflight"
    assert result.failure["exception_type"] == "PythonPreflightError"
    assert result.failure["line"] == 1


@pytest.mark.asyncio
async def test_repair_dependency_preflight_skips_docker_execution(client, settings) -> None:
    project = client.post("/api/projects", json={"name": "Repair dependency preflight"}).json()

    class CountingExecutor:
        calls = 0

        async def execute(self, workspace_path, script_path, execution_id):
            self.calls += 1
            raise AssertionError("Docker must not run for a provably incomplete repair")

    code = """\
fields = ["entity_id"]
entities = source[fields].copy()
summary = entities.groupby(["group_a", "group_b"]).size()
"""
    executor = CountingExecutor()
    with client.app.state.database.session() as session:
        result = await PythonExecutionService(
            session, PathResolver(settings.workspace_root), executor
        ).execute(
            project["id"],
            "analysis_repaired.py",
            code,
            require_artifact_change=True,
        )

    assert executor.calls == 0
    assert result.status == "failed"
    assert result.docker_executed is False
    assert result.failure["source"] == "preflight"
    assert result.failure["exception_type"] == "PythonSchemaDependencyError"
    assert "group_a" in result.failure["message"]
    assert "group_b" in result.failure["message"]


def test_artifact_catalog_records_real_table_schema_and_row_count(client, settings) -> None:
    project = client.post("/api/projects", json={"name": "Schema catalog"}).json()
    resolver = PathResolver(settings.workspace_root)
    path = resolver.resolve(project["id"], "data/intermediate.csv")
    path.write_text("date,metric_x\n2026-01-01,1\n2026-01-02,2\n", encoding="utf-8")
    with client.app.state.database.session() as session:
        ArtifactService(session).register(
            project["id"], "data/intermediate.csv", path.stat().st_size
        )
        catalog = ReportInputCollector(session, resolver, None).catalog(project["id"])

    entry = next(item for item in catalog if item.path == "data/intermediate.csv")
    assert entry.structure["row_count"] == 2
    assert entry.structure["columns"] == [
        {
            "name": "date",
            "dtype": "string",
            "type": "string",
            "semantic_type": "date",
            "display_label": "date",
        },
        {
            "name": "metric_x",
            "dtype": "number",
            "type": "number",
            "semantic_type": "integer",
            "display_label": "Metric X",
        },
    ]


@pytest.mark.asyncio
async def test_execution_persists_valid_local_contract_before_complete_analysis(
    client, settings
) -> None:
    project = client.post("/api/projects", json={"name": "Creation contract"}).json()

    class FakeExecutor:
        async def execute(self, workspace_path, script_path, execution_id):
            (workspace_path / "data" / "category_metrics.csv").write_text(
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

    contract = AnalysisArtifactContract.model_validate(
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
                }
            ],
        }
    )
    resolver = PathResolver(settings.workspace_root)
    with client.app.state.database.session() as session:
        result = await PythonExecutionService(session, resolver, FakeExecutor()).execute(
            project["id"],
            "category.py",
            "print('done')",
            expected_artifacts=["data/category_metrics.csv"],
            artifact_contracts=[contract],
        )
        artifact = session.scalar(
            select(Artifact).where(
                Artifact.project_id == project["id"],
                Artifact.path == "data/category_metrics.csv",
            )
        )

    metrics = resolver.resolve(project["id"], "analysis/metrics.json").read_text(encoding="utf-8")
    assert result.artifact_contract_issues == []
    assert result.registered_report_schemas == ["data/category_metrics.csv"]
    assert result.registered_reusable_metrics == ["metric_x"]
    assert artifact.report_schema_json is not None
    assert '"origin_task_id":"task_category"' in artifact.report_schema_json
    assert '"grain":"category_a"' in artifact.report_schema_json
    assert '"metric_id": "metric_x"' in metrics
    assert '"metric_scope": "reusable_measure"' in metrics


@pytest.mark.asyncio
async def test_invalid_local_contract_keeps_artifact_but_does_not_make_it_report_ready(
    client, settings
) -> None:
    project = client.post("/api/projects", json={"name": "Invalid contract"}).json()

    class FakeExecutor:
        async def execute(self, workspace_path, script_path, execution_id):
            (workspace_path / "data" / "category_metrics.csv").write_text(
                "category_a,metric_x\nA,not-a-number\nB,also-text\n",
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

    contract = AnalysisArtifactContract.model_validate(
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
                    "grain": "category_a",
                    "definition": "Sum of metric_x by category_a",
                    "source_artifact": "data/category_metrics.csv",
                    "source_field": "metric_x",
                }
            ],
        }
    )
    resolver = PathResolver(settings.workspace_root)
    with client.app.state.database.session() as session:
        result = await PythonExecutionService(session, resolver, FakeExecutor()).execute(
            project["id"],
            "category.py",
            "print('done')",
            expected_artifacts=["data/category_metrics.csv"],
            artifact_contracts=[contract],
        )
        artifact = session.scalar(
            select(Artifact).where(
                Artifact.project_id == project["id"],
                Artifact.path == "data/category_metrics.csv",
            )
        )

    assert result.status == "success"
    assert result.artifact_contract_issues[0]["code"] == "ARTIFACT_CONTRACT_INVALID"
    assert artifact is not None
    assert artifact.report_schema_json is None
    assert not resolver.resolve(project["id"], "analysis/metrics.json").exists()


@pytest.mark.asyncio
async def test_scalar_artifact_contract_registers_verified_json_metrics(client, settings) -> None:
    project = client.post("/api/projects", json={"name": "Scalar registry"}).json()
    resolver = PathResolver(settings.workspace_root)

    class FakeExecutor:
        async def execute(self, workspace_path, script_path, execution_id):
            target = workspace_path / "data" / "summary.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"total_amount": 7}), encoding="utf-8")
            return ExecutionResult(
                execution_id=execution_id,
                status="success",
                exit_code=0,
                stdout="done",
                stderr="",
                duration_ms=1,
                script_path=script_path,
            )

    action = AgentActionResponse.model_validate(
        {
            "action": "execute_python",
            "task_id": "task_summary",
            "filename": "summary.py",
            "code": "print('done')",
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

    with client.app.state.database.session() as session:
        result = await PythonExecutionService(session, resolver, FakeExecutor()).execute(
            project["id"],
            action.filename,
            action.code,
            expected_artifacts=action.expected_artifacts,
            scalar_artifact_contracts=action.scalar_artifact_contracts,
        )

    assert result.status == "success"
    assert result.artifact_contract_issues == []
    assert result.registered_scalar_metrics == ["total_amount"]
    payload = json.loads(
        resolver.resolve(project["id"], "analysis/metrics.json").read_text(encoding="utf-8")
    )
    assert payload["metrics"][0]["metric_id"] == "total_amount"
    assert payload["metrics"][0]["metric_scope"] == "scalar_evidence"


@pytest.mark.asyncio
async def test_scalar_artifact_contract_rejects_unverifiable_value(client, settings) -> None:
    project = client.post("/api/projects", json={"name": "Invalid scalar registry"}).json()
    resolver = PathResolver(settings.workspace_root)

    class FakeExecutor:
        async def execute(self, workspace_path, script_path, execution_id):
            target = workspace_path / "data" / "summary.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"total_amount": 7}), encoding="utf-8")
            return ExecutionResult(
                execution_id=execution_id,
                status="success",
                exit_code=0,
                stdout="done",
                stderr="",
                duration_ms=1,
                script_path=script_path,
            )

    contract = {
        "artifact_path": "data/summary.json",
        "metrics": [
            {
                "metric_id": "total_amount",
                "label": "Total amount",
                "value": 8,
                "aggregation": "sum",
                "semantic_type": "measure",
                "unit_family": "currency",
                "definition": "Sum of the verified amount",
                "source_artifact": "data/summary.json",
                "source_field": "total_amount",
            }
        ],
    }

    with client.app.state.database.session() as session:
        result = await PythonExecutionService(session, resolver, FakeExecutor()).execute(
            project["id"],
            "summary.py",
            "print('done')",
            expected_artifacts=["data/summary.json"],
            scalar_artifact_contracts=[ScalarArtifactContract.model_validate(contract)],
        )

    assert result.status == "success"
    assert result.artifact_contract_issues[0]["code"] == "SCALAR_ARTIFACT_CONTRACT_INVALID"
    assert result.artifact_contract_issues[0]["provenance_issues"][0]["code"] == (
        "SCALAR_METRIC_VALUE_MISMATCH"
    )
    assert not resolver.resolve(project["id"], "analysis/metrics.json").exists()
