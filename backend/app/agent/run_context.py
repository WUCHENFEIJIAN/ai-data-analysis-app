import json
from pathlib import Path
from typing import Any

from app.agent.context import ContextBuilder
from app.models import AnalysisRun, Execution
from app.services.artifact_schema import ArtifactSchemaInspector
from app.services.python_failure import (
    dataframe_dependency_issues,
    parse_python_failure,
    script_fingerprint,
)


class RunContextBuilder:
    PACKAGING_CONTEXT_MAX_CHARS = 18_000
    PACKAGING_ARTIFACT_LIMIT = 40
    PACKAGING_RESULT_FILE_MAX_BYTES = 64_000
    PACKAGING_RESULT_LIMIT = 20

    def build(
        self,
        run: AnalysisRun,
        skill: str,
        profile: dict[str, Any],
        workspace: Path,
        plan: dict[str, Any] | None,
        latest_execution: Execution | None,
        recent_executions: list[Execution] | None = None,
    ) -> list[dict[str, str]]:
        messages = ContextBuilder().build_planning_context(run.user_request, profile, skill)
        state = {
            "run_id": run.id,
            "state": run.state,
            "step": run.step_count,
            "execution_count": run.execution_count,
            "plan": plan,
            "workspace_index": self.workspace_index(workspace),
            "latest_execution": self.execution_summary(latest_execution, workspace),
            "analysis_packaging_context": self.analysis_packaging_context(
                workspace,
                plan,
                recent_executions or ([latest_execution] if latest_execution else []),
            ),
        }
        if latest_execution is not None and latest_execution.status != "success":
            state["python_repair"] = self.python_repair_context(
                workspace,
                latest_execution,
                recent_executions or [latest_execution],
                plan,
                profile,
            )
        messages.append(
            {
                "role": "user",
                "content": (
                    '<runtime_state trust="application-state">\n'
                    f"{json.dumps(state, ensure_ascii=False, separators=(',', ':'))}\n"
                    "</runtime_state>"
                ),
            }
        )
        return messages

    @classmethod
    def analysis_packaging_context(
        cls,
        workspace: Path,
        plan: dict[str, Any] | None,
        executions: list[Execution],
    ) -> dict[str, Any]:
        """Build bounded Analysis memory from existing plans and generated Artifacts."""
        inspector = ArtifactSchemaInspector()
        execution_sources: list[tuple[Execution, str]] = []
        for execution in executions:
            source = cls._script_source(workspace, execution.script_path)
            if source is not None:
                execution_sources.append((execution, source))

        artifact_catalog: list[dict[str, Any]] = []
        compact_results: list[dict[str, Any]] = []
        generated_by_task: dict[str, list[str]] = {}
        data_root = workspace / "data"
        if data_root.is_dir():
            paths = sorted(
                (
                    path
                    for path in data_root.rglob("*")
                    if path.is_file() and path.suffix.lower() in {".csv", ".json", ".parquet"}
                ),
                key=lambda path: path.as_posix(),
            )[: cls.PACKAGING_ARTIFACT_LIMIT]
            for path in paths:
                relative = path.relative_to(workspace).as_posix()
                origin_task_ids = cls._artifact_origin_tasks(relative, execution_sources)
                for task_id in origin_task_ids:
                    generated_by_task.setdefault(task_id, []).append(relative)
                structure = inspector.inspect(path)
                if structure is None:
                    continue
                artifact_catalog.append(
                    cls._compact_artifact_catalog_entry(
                        relative,
                        structure,
                        origin_task_ids,
                    )
                )
                if (
                    path.suffix.lower() == ".json"
                    and structure.get("record_kind") == "object"
                    and path.stat().st_size <= cls.PACKAGING_RESULT_FILE_MAX_BYTES
                    and len(compact_results) < cls.PACKAGING_RESULT_LIMIT
                ):
                    result = cls._compact_json_result(path)
                    if result is not None:
                        compact_results.append(
                            {
                                "artifact_path": relative,
                                "origin_task_ids": origin_task_ids,
                                "result": result,
                            }
                        )

        task_coverage = cls._plan_task_coverage(plan, executions, generated_by_task)
        context = {
            "instruction": (
                "Package the already-computed Analysis into complete_analysis. This context is "
                "Analysis memory, not a request to rerun Python. Preserve relevant topic "
                "coverage; summary brevity does not imply Findings brevity."
            ),
            "plan_coverage": task_coverage,
            "artifact_catalog": artifact_catalog,
            "structured_results": compact_results,
            "available_metrics": cls.available_metric_catalog(workspace),
        }
        cls._fit_packaging_budget(context)
        return context

    @staticmethod
    def available_metric_catalog(workspace: Path) -> list[dict[str, Any]]:
        """Expose only the canonical creation-time metric directory to the LLM."""
        target = workspace / "analysis" / "metrics.json"
        if not target.is_file():
            return []
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            values = payload.get("metrics", payload) if isinstance(payload, dict) else payload
            if not isinstance(values, list):
                return []
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return []
        catalog: list[dict[str, Any]] = []
        for item in values[:120]:
            if not isinstance(item, dict) or not isinstance(item.get("metric_id"), str):
                continue
            catalog.append(
                {
                    key: item[key]
                    for key in (
                        "metric_id",
                        "metric_scope",
                        "label",
                        "source_artifact",
                        "source_field",
                        "grain",
                        "unit",
                        "unit_family",
                    )
                    if item.get(key) is not None
                }
            )
        return catalog

    @staticmethod
    def _artifact_origin_tasks(
        relative_path: str,
        execution_sources: list[tuple[Execution, str]],
    ) -> list[str]:
        native_path = relative_path.replace("/", "\\")
        task_ids = {
            execution.task_id
            for execution, source in execution_sources
            if execution.status == "success"
            and execution.task_id
            and (relative_path in source or native_path in source)
        }
        return sorted(task_ids)

    @staticmethod
    def _compact_artifact_catalog_entry(
        relative_path: str,
        structure: dict[str, Any],
        origin_task_ids: list[str],
    ) -> dict[str, Any]:
        columns = structure.get("columns")
        fields = []
        if isinstance(columns, list):
            for column in columns[:60]:
                if not isinstance(column, dict):
                    continue
                fields.append(
                    {
                        key: column.get(key)
                        for key in (
                            "name",
                            "type",
                            "semantic_type",
                            "role",
                            "metric_ref",
                        )
                        if column.get(key) is not None
                    }
                )
        return {
            "artifact_path": relative_path,
            "artifact_kind": structure.get("record_kind"),
            "row_count": structure.get("row_count"),
            "fields": fields,
            "origin_task_ids": origin_task_ids,
        }

    @classmethod
    def _compact_json_result(cls, path: Path) -> Any | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return cls._compact_value(value)

    @classmethod
    def _compact_value(cls, value: Any, depth: int = 0) -> Any:
        if depth >= 4:
            return "[nested result omitted]"
        if isinstance(value, dict):
            return {
                str(key)[:120]: cls._compact_value(item, depth + 1)
                for key, item in list(value.items())[:40]
            }
        if isinstance(value, list):
            return [cls._compact_value(item, depth + 1) for item in value[:12]]
        if isinstance(value, str):
            return value[:500]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:500]

    @staticmethod
    def _plan_task_coverage(
        plan: dict[str, Any] | None,
        executions: list[Execution],
        generated_by_task: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        if not isinstance(plan, dict):
            return []
        execution_statuses: dict[str, list[str]] = {}
        for execution in executions:
            if execution.task_id:
                execution_statuses.setdefault(execution.task_id, []).append(execution.status)
        coverage = []
        for task in plan.get("tasks", []):
            if not isinstance(task, dict) or not isinstance(task.get("id"), str):
                continue
            task_id = task["id"]
            statuses = execution_statuses.get(task_id, [])
            if "success" in statuses:
                status = "completed"
            elif statuses:
                status = "attempted_failed"
            else:
                status = "pending"
            coverage.append(
                {
                    "task_id": task_id,
                    "title": task.get("title"),
                    "objective": task.get("goal"),
                    "status": status,
                    "generated_artifact_refs": generated_by_task.get(task_id, []),
                }
            )
        return coverage

    @classmethod
    def _fit_packaging_budget(cls, context: dict[str, Any]) -> None:
        def size() -> int:
            return len(json.dumps(context, ensure_ascii=False, separators=(",", ":")))

        results = context["structured_results"]
        artifacts = context["artifact_catalog"]
        while size() > cls.PACKAGING_CONTEXT_MAX_CHARS and results:
            results.pop()
        while size() > cls.PACKAGING_CONTEXT_MAX_CHARS and artifacts:
            artifacts.pop()

    @staticmethod
    def workspace_index(workspace: Path) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for directory in workspace.iterdir():
            if not directory.is_dir() or directory.name == "logs":
                continue
            index[directory.name] = sorted(
                path.relative_to(directory).as_posix()
                for path in directory.rglob("*")
                if path.is_file()
            )
        return index

    @staticmethod
    def execution_summary(
        execution: Execution | None, workspace: Path | None = None
    ) -> dict[str, Any] | None:
        if execution is None:
            return None
        summary = {
            "id": execution.id,
            "status": execution.status,
            "exit_code": execution.exit_code,
            "stdout": execution.stdout[-4000:],
            "stderr": execution.stderr[-4000:],
            "script_path": execution.script_path,
        }
        if workspace is not None:
            source = RunContextBuilder._script_source(workspace, execution.script_path)
            if source is not None:
                summary["script_fingerprint"] = script_fingerprint(source)
        return summary

    @classmethod
    def python_repair_context(
        cls,
        workspace: Path,
        execution: Execution,
        recent_executions: list[Execution],
        plan: dict[str, Any] | None,
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        source = cls._script_source(workspace, execution.script_path) or ""
        failure = parse_python_failure(execution.stderr, execution.script_path, source)
        prior_failures: list[dict[str, object]] = []
        for prior in recent_executions:
            if prior.id == execution.id or prior.status == "success":
                continue
            prior_source = cls._script_source(workspace, prior.script_path) or ""
            parsed = parse_python_failure(prior.stderr, prior.script_path, prior_source)
            prior_failures.append(
                {
                    "fingerprint": parsed.fingerprint,
                    "semantic_fingerprint": parsed.semantic_fingerprint,
                    "script_fingerprint": script_fingerprint(prior_source),
                    "exception_type": parsed.exception_type,
                    "message": parsed.normalized_message,
                    "line": parsed.line,
                    "failing_line": parsed.failing_line,
                }
            )
        return {
            "instruction": (
                "Make the minimum complete root-cause repair to the supplied source while "
                "preserving valid logic and outputs. For DataFrame schema failures such as "
                "missing columns, invalid index selections, or merge suffix collisions, identify "
                "the affected DataFrame and its producing projection, merge, rename, aggregation, "
                "or reassignment; then audit every explicit downstream column reference to that "
                "DataFrame until it is reassigned. Repair the producer so all required existing "
                "or explicitly derived columns remain available. Use only columns confirmed by "
                "known_input_schemas or created by the script. Do not suppress the error, delete "
                "analytical outputs, invent columns, add dataset-specific fallbacks, or refactor "
                "unrelated logic. Return a new execute_python Action because the application "
                "preserves every script revision."
            ),
            "stage_goal": cls._stage_goal(plan, execution.task_id),
            "failed_source": source,
            "failure": failure.as_dict(),
            "input_artifacts": cls.artifact_schemas(workspace, source),
            "known_input_schemas": cls.dataset_schemas(profile),
            "dataframe_dependency_hints": [
                issue.as_dict() for issue in dataframe_dependency_issues(source)[:20]
            ],
            "previous_failures": prior_failures[-5:],
        }

    @staticmethod
    def dataset_schemas(profile: dict[str, Any]) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for file_entry in profile.get("files", [])[:20]:
            if not isinstance(file_entry, dict):
                continue
            for sheet in file_entry.get("sheets", [])[:20]:
                if not isinstance(sheet, dict):
                    continue
                columns = [
                    column.get("name")
                    for column in sheet.get("columns", [])[:200]
                    if isinstance(column, dict) and isinstance(column.get("name"), str)
                ]
                schemas.append(
                    {
                        "path": file_entry.get("path"),
                        "sheet": sheet.get("name"),
                        "columns": columns,
                    }
                )
                if len(schemas) >= 30:
                    return schemas
        return schemas

    @staticmethod
    def artifact_schemas(workspace: Path, source: str = "") -> list[dict[str, Any]]:
        inspector = ArtifactSchemaInspector()
        entries: list[dict[str, Any]] = []
        candidates: list[tuple[int, Path]] = []
        for directory in ("input", "data", "context"):
            root = workspace / directory
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in {".csv", ".json", ".parquet"}:
                    continue
                relative = path.relative_to(workspace).as_posix()
                candidates.append((0 if relative in source else 1, path))
        for _, path in sorted(candidates, key=lambda item: (item[0], item[1].as_posix()))[:30]:
            structure = inspector.inspect(path)
            if structure is not None:
                entries.append(
                    {
                        "artifact_path": path.relative_to(workspace).as_posix(),
                        "referenced_by_script": path.relative_to(workspace).as_posix() in source,
                        "schema": structure,
                    }
                )
        return entries

    @staticmethod
    def _script_source(workspace: Path, script_path: str) -> str | None:
        try:
            path = (workspace / script_path).resolve()
            path.relative_to(workspace.resolve())
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            return None

    @staticmethod
    def _stage_goal(plan: dict[str, Any] | None, task_id: str | None) -> Any:
        if not isinstance(plan, dict):
            return None
        for task in plan.get("tasks", []):
            if isinstance(task, dict) and task.get("id") == task_id:
                return {
                    "task_id": task_id,
                    "title": task.get("title"),
                    "goal": task.get("goal"),
                }
        return {"objective": plan.get("objective"), "analysis_topic": plan.get("analysis_topic")}
