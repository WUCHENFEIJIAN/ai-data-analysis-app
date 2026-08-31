import hashlib
import json
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.sandbox.executor import SandboxExecutor
from app.schemas.execution import ExecutionResult
from app.services.artifacts import ArtifactDetector, ArtifactService
from app.services.metric_contract import MetricDefinition, MetricValidator
from app.services.metric_provenance import validate_scalar_artifact_contract
from app.services.python_failure import (
    parse_python_failure,
    preflight_failure,
    script_fingerprint,
)
from app.services.report_ready_artifacts import (
    AnalysisArtifactContract,
    ScalarArtifactContract,
    validate_report_ready_artifacts,
)
from app.services.scripts import ScriptManager
from app.services.workspace import PathResolver


def contract_submission_drop_issues(
    submitted_paths: list[str],
    accepted_paths: list[str],
    issues: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Detect a parsed artifact contract that disappears without an outcome."""

    issue_paths = {
        str(issue["artifact_path"])
        for issue in issues
        if issue.get("artifact_path") and str(issue.get("code", "")).startswith(
            "ARTIFACT_CONTRACT_"
        )
    }
    resolved_paths = set(accepted_paths) | issue_paths
    return [
        {
            "code": "CONTRACT_SUBMISSION_DROPPED",
            "artifact_path": path,
            "message": (
                "submitted artifact contract produced neither an acceptance nor a validation "
                "issue"
            ),
        }
        for path in dict.fromkeys(submitted_paths)
        if path not in resolved_paths
    ]


class PythonExecutionService:
    def __init__(
        self,
        session: Session,
        resolver: PathResolver,
        executor: SandboxExecutor,
    ) -> None:
        self.session = session
        self.resolver = resolver
        self.executor = executor

    async def execute(
        self,
        project_id: str,
        suggested_name: str,
        code: str,
        expected_artifacts: list[str] | None = None,
        require_artifact_change: bool = False,
        artifact_contracts: list[AnalysisArtifactContract] | None = None,
        scalar_artifact_contracts: list[ScalarArtifactContract] | None = None,
    ) -> ExecutionResult:
        return await self.execute_with_id(
            project_id,
            suggested_name,
            code,
            f"exec_{uuid.uuid4().hex}",
            expected_artifacts,
            require_artifact_change,
            artifact_contracts,
            scalar_artifact_contracts,
        )

    async def execute_with_id(
        self,
        project_id: str,
        suggested_name: str,
        code: str,
        execution_id: str,
        expected_artifacts: list[str] | None = None,
        require_artifact_change: bool = False,
        artifact_contracts: list[AnalysisArtifactContract] | None = None,
        scalar_artifact_contracts: list[ScalarArtifactContract] | None = None,
    ) -> ExecutionResult:
        workspace = self.resolver.project_root(project_id)
        detector = ArtifactDetector()
        before = detector.snapshot(workspace)
        script_path = ScriptManager(self.resolver).save(project_id, suggested_name, code)
        failure = preflight_failure(
            code,
            script_path,
            check_dataframe_dependencies=require_artifact_change,
        )
        if failure is not None:
            result = ExecutionResult(
                execution_id=execution_id,
                status="failed",
                exit_code=None,
                stdout="",
                stderr=f"{failure.exception_type}: {failure.message}",
                duration_ms=0,
                script_path=script_path,
                docker_executed=False,
                failure=failure.as_dict(),
            )
        else:
            result = await self.executor.execute(workspace, script_path, execution_id)
        after = detector.snapshot(workspace)
        result.new_artifacts = detector.changed(before, after)
        result.script_fingerprint = script_fingerprint(code)
        result.artifact_fingerprint_before = _snapshot_fingerprint(before)
        result.artifact_fingerprint_after = _snapshot_fingerprint(after)
        changed_outputs = [
            path for path in result.new_artifacts if not path.startswith("scripts/")
        ]
        missing_outputs = sorted(set(expected_artifacts or []) - set(changed_outputs))
        if result.status == "success" and (
            missing_outputs or (require_artifact_change and not changed_outputs)
        ):
            message = (
                "Expected Artifacts were not created or changed: " + ", ".join(missing_outputs)
                if missing_outputs
                else "Repair script did not create or change any output Artifact"
            )
            result.status = "failed"
            result.stderr = f"PythonOutputValidationError: {message}"
            result.failure = parse_python_failure(
                result.stderr,
                script_path,
                code,
                source="postcondition",
                exception_type="PythonOutputValidationError",
                message=message,
            ).as_dict()
        if result.status != "success" and result.failure is None:
            result.failure = parse_python_failure(
                result.stderr, script_path, code, source="docker"
            ).as_dict()
        artifact_service = ArtifactService(self.session)
        for path in result.new_artifacts:
            artifact_service.register(
                project_id, path, self.resolver.resolve(project_id, path).stat().st_size
            )
        if result.status == "success" and (artifact_contracts or scalar_artifact_contracts):
            self._persist_artifact_contracts(
                project_id,
                result,
                artifact_service,
                artifact_contracts,
                scalar_artifact_contracts,
            )
        return result

    def _persist_artifact_contracts(
        self,
        project_id: str,
        result: ExecutionResult,
        artifact_service: ArtifactService,
        contracts: list[AnalysisArtifactContract],
        scalar_contracts: list[ScalarArtifactContract] | None = None,
    ) -> None:
        registry = self._load_metric_registry(project_id)
        initial_registry = dict(registry)
        declarations = []
        changed = set(result.new_artifacts)
        for contract in contracts or []:
            if contract.artifact_path not in changed:
                result.artifact_contract_issues.append(
                    {
                        "code": "ARTIFACT_CONTRACT_OUTPUT_NOT_CHANGED",
                        "artifact_path": contract.artifact_path,
                    }
                )
                continue
            conflicts = [
                metric.metric_id
                for metric in contract.metrics
                if metric.metric_id in registry
                and registry[metric.metric_id].model_dump(mode="json")
                != metric.model_dump(mode="json")
            ]
            if conflicts:
                result.artifact_contract_issues.append(
                    {
                        "code": "ARTIFACT_CONTRACT_METRIC_CONFLICT",
                        "artifact_path": contract.artifact_path,
                        "metric_ids": sorted(conflicts),
                    }
                )
                continue
            candidate_registry = dict(registry)
            candidate_registry.update({metric.metric_id: metric for metric in contract.metrics})
            metric_issues = MetricValidator.issues(candidate_registry.values())
            declaration = contract.report_ready_declaration()
            report_ready_issues = validate_report_ready_artifacts(
                self.resolver,
                project_id,
                [declaration],
                candidate_registry.values(),
            )
            if metric_issues or report_ready_issues:
                result.artifact_contract_issues.append(
                    {
                        "code": "ARTIFACT_CONTRACT_INVALID",
                        "artifact_path": contract.artifact_path,
                        "metric_issues": metric_issues,
                        "report_ready_issues": report_ready_issues,
                    }
                )
                continue
            registry = candidate_registry
            declarations.append(declaration)
            result.registered_report_schemas.append(contract.artifact_path)
            result.registered_reusable_metrics.extend(
                metric.metric_id for metric in contract.metrics
            )

        for contract in scalar_contracts or []:
            if contract.artifact_path not in changed:
                result.artifact_contract_issues.append(
                    {
                        "code": "SCALAR_ARTIFACT_OUTPUT_NOT_CHANGED",
                        "artifact_path": contract.artifact_path,
                    }
                )
                continue
            conflicts = [
                metric.metric_id
                for metric in contract.metrics
                if metric.metric_id in registry
                and registry[metric.metric_id].model_dump(mode="json")
                != metric.model_dump(mode="json")
            ]
            if conflicts:
                result.artifact_contract_issues.append(
                    {
                        "code": "SCALAR_ARTIFACT_METRIC_CONFLICT",
                        "artifact_path": contract.artifact_path,
                        "metric_ids": sorted(conflicts),
                    }
                )
                continue
            provenance_issues = validate_scalar_artifact_contract(
                self.resolver, project_id, contract
            )
            candidate_registry = dict(registry)
            candidate_registry.update({metric.metric_id: metric for metric in contract.metrics})
            metric_issues = MetricValidator.issues(candidate_registry.values())
            if provenance_issues or metric_issues:
                result.artifact_contract_issues.append(
                    {
                        "code": "SCALAR_ARTIFACT_CONTRACT_INVALID",
                        "artifact_path": contract.artifact_path,
                        "metric_issues": metric_issues,
                        "provenance_issues": provenance_issues,
                    }
                )
                continue
            registry = candidate_registry
            result.registered_scalar_metrics.extend(
                metric.metric_id for metric in contract.metrics
            )

        result.artifact_contract_issues.extend(
            contract_submission_drop_issues(
                [contract.artifact_path for contract in contracts or []],
                result.registered_report_schemas,
                result.artifact_contract_issues,
            )
        )

        if registry == initial_registry:
            if declarations:
                artifact_service.upsert_report_schemas(project_id, declarations)
            return
        metrics_target = self.resolver.resolve(project_id, "analysis/metrics.json")
        self._write_metric_registry(metrics_target, registry.values())
        artifact_service.register(
            project_id,
            "analysis/metrics.json",
            metrics_target.stat().st_size,
        )
        if declarations:
            artifact_service.upsert_report_schemas(project_id, declarations)

    def _load_metric_registry(self, project_id: str) -> dict[str, MetricDefinition]:
        target = self.resolver.resolve(project_id, "analysis/metrics.json")
        if not target.is_file():
            return {}
        payload = json.loads(target.read_text(encoding="utf-8"))
        values = payload.get("metrics", payload) if isinstance(payload, dict) else payload
        if not isinstance(values, list):
            raise ValueError("analysis/metrics.json must contain a metrics array")
        metrics = [MetricDefinition.model_validate(item) for item in values]
        MetricValidator.validate(metrics)
        return {metric.metric_id: metric for metric in metrics}

    @staticmethod
    def _write_metric_registry(target: Path, metrics: object) -> None:
        payload = {
            "schema_version": "1.0",
            "metrics": [metric.model_dump(mode="json") for metric in metrics],
        }
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(target)


def _snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    digest = hashlib.sha256()
    for path, entry in sorted(snapshot.items()):
        if path.startswith("scripts/"):
            continue
        digest.update(path.encode("utf-8"))
        digest.update(repr(entry).encode("utf-8"))
    return digest.hexdigest()

