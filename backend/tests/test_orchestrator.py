import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent.orchestrator import AnalysisOrchestrator
from app.core.errors import LLMError
from app.llm.mock import MockLLMProvider
from app.models import AnalysisRun, AnalysisTask, Execution, RuntimeEvent
from app.schemas.actions import AgentActionResponse, DeclareReportEvidenceAction
from app.schemas.execution import ExecutionResult
from app.services.analysis_runs import AnalysisRunService, recover_interrupted_runs
from app.services.artifacts import ArtifactService

PLAN = {
    "action": "create_plan",
    "title": "Sales analysis",
    "objective": "Find sales drivers",
    "tasks": [{"id": "task_1", "title": "Overview", "goal": "Calculate totals", "sequence": 1}],
}
EXECUTE = {
    "action": "execute_python",
    "task_id": "task_1",
    "filename": "overview.py",
    "code": "print('analysis')",
    "purpose": "Calculate verified metrics",
}
COMPLETE = {
    "action": "complete_analysis",
    "summary": "Sales are concentrated",
    "metrics": [
        {
            "metric_id": "sales",
            "label": "Sales",
            "value": 100,
            "aggregation": "sum",
            "semantic_type": "measure",
            "unit_family": "currency",
            "unit": "元",
            "definition": "Sum of the verified values",
            "source_artifact": "data/result.csv",
        }
    ],
    "findings": [
        {
            "id": "finding_1",
            "title": "East leads",
            "evidence": ["data/result.csv records East=100"],
            "risk": "Concentration",
            "recommendation": "Validate other regions",
            "related_artifacts": ["data/result.csv"],
            "claims": [
                {
                    "claim_id": "claim_east",
                    "statement": "East has a verified value of 100",
                    "priority": "primary",
                    "strength": 0.9,
                    "evidence_metric_ids": ["sales"],
                    "evidence_artifact_paths": ["data/result.csv"],
                }
            ],
        }
    ],
}
REPORT = {"action": "generate_report", "title": "Sales report", "style": "FT"}
REPORT_HTML = (
    "<!doctype html><html><head><style>"
    "body{max-width:1200px;margin:0 auto;padding:40px 48px}"
    "</style></head><body><h1>Sales report</h1>"
    "<p>East: 100</p></body></html>"
)


def invalid_evidence_declaration(source_artifact: str) -> AgentActionResponse:
    return AgentActionResponse.model_validate(
        {
            "action": "declare_report_evidence",
            "schema_version": "1.0",
            "metrics": [
                {
                    "metric_id": "sales",
                    "label": "Measured value",
                    "value": 100,
                    "aggregation": "sum",
                    "semantic_type": "measure",
                    "unit_family": "currency",
                    "definition": "Sum of the verified values",
                    "source_artifact": source_artifact,
                }
            ],
            "kpis": [],
            "artifacts": [],
        }
    )


def valid_evidence_declaration() -> dict:
    return {
        "action": "declare_report_evidence",
        "schema_version": "1.0",
        "metrics": [
            {
                "metric_id": "sales",
                "label": "Measured value",
                "value": 100,
                "aggregation": "sum",
                "semantic_type": "measure",
                "unit_family": "currency",
                "definition": "Sum of the verified values",
                "source_artifact": "data/result.csv",
            }
        ],
        "kpis": [
            {
                "id": "total_sales",
                "label": "Total sales",
                "metric": "sales",
                "artifact_path": "data/result.csv",
                "selector": {
                    "type": "table",
                    "records_path": [],
                    "row": 0,
                    "field": "sales",
                },
                "format": "currency",
                "finding_ids": ["finding_1"],
                "purpose": "Show the verified total",
                "role": "evidence",
                "supports_claim_ids": ["claim_east"],
            }
        ],
        "artifacts": [
            {
                "artifact_path": "data/result.csv",
                "usage": "visual_source",
                "finding_ids": ["finding_1"],
                "purpose": "Compare verified segments",
                "supports_claim_ids": ["claim_east"],
                "chart": {
                    "chart_type": "bar",
                    "title": "Segment comparison",
                    "x_field": "region",
                    "series": [
                        {"field": "sales", "label": "Sales", "metric": "sales"}
                    ],
                    "source_caption": "Source: result.csv",
                    "supports_claim_ids": ["claim_east"],
                },
            }
        ],
    }


class FakeExecutor:
    def __init__(self, statuses: list[str] | None = None) -> None:
        self.statuses = list(statuses or ["success"])
        self.stopped: list[str] = []

    async def execute(self, workspace: Path, script_path: str, execution_id: str):
        status = self.statuses.pop(0)
        if status == "success":
            (workspace / "data" / "result.csv").write_text("region,sales\nEast,100\n")
            (workspace / "analysis" / "report_evidence.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "metrics": [
                            {
                                "metric_id": "sales",
                                "label": "Measured value",
                                "value": 100,
                                "aggregation": "sum",
                                "semantic_type": "measure",
                                "unit_family": "currency",
                                "unit": "元",
                                "definition": "Sum of the verified values",
                                "source_artifact": "data/result.csv",
                            }
                        ],
                        "kpis": [
                            {
                                "id": "total_sales",
                                "label": "总销售额",
                                "metric": "sales",
                                "artifact_path": "data/result.csv",
                                "selector": {
                                    "type": "table",
                                    "records_path": [],
                                    "row": 0,
                                    "field": "sales",
                                },
                                "format": "currency",
                                "decimals": 0,
                                "unit": "元",
                                "finding_ids": ["finding_1"],
                                "purpose": "展示已验证的销售额指标",
                                "role": "evidence",
                                "supports_claim_ids": ["claim_east"],
                            }
                        ],
                        "artifacts": [
                            {
                                "artifact_path": "data/result.csv",
                                "usage": "visual_source",
                                "finding_ids": ["finding_1"],
                                "purpose": "支持区域销售额比较",
                                "supports_claim_ids": ["claim_east"],
                                "chart": {
                                    "chart_type": "bar",
                                    "title": "区域销售额",
                                    "x_field": "region",
                                    "series": [
                                        {
                                            "field": "sales",
                                            "label": "销售额",
                                            "metric": "sales",
                                            "format": "currency",
                                            "unit": "元",
                                        }
                                    ],
                                    "source_caption": "来源：data/result.csv",
                                    "supports_claim_ids": ["claim_east"],
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        return ExecutionResult(
            execution_id=execution_id,
            status=status,
            exit_code=0 if status == "success" else 1,
            stdout="calculated" if status == "success" else "",
            stderr="KeyError: sales" if status != "success" else "",
            duration_ms=5,
            script_path=script_path,
        )

    async def stop(self, execution_id: str) -> bool:
        self.stopped.append(execution_id)
        return True


def prepare_run(client, project_name: str = "Agent") -> str:
    project = client.post("/api/projects", json={"name": project_name}).json()
    upload = client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("sales.csv", "region,sales\nEast,100\n", "text/csv")},
    )
    assert upload.json()["profile_status"] == "completed"
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze sales")
        return run.id


def prepare_evidence_repair_run(client, settings, orchestrator: AnalysisOrchestrator) -> str:
    run_id = prepare_run(client, "Evidence declaration repair")
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        result_path = settings.workspace_root / run.project_id / "data" / "result.csv"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text("region,sales\nEast,100\n", encoding="utf-8")
        ArtifactService(session).register(
            run.project_id, "data/result.csv", result_path.stat().st_size
        )
    complete = AgentActionResponse.model_validate(COMPLETE).root
    assert orchestrator._complete_analysis(run_id, complete)
    with client.app.state.database.session() as session:
        service = AnalysisRunService(session)
        service.event(
            run_id,
            "analysis.artifact_preparation_required",
            {"repair_route": "evidence_contract"},
        )
        service.get(run_id).state = "EVALUATE"
    return run_id


@pytest.mark.asyncio
async def test_agent_loop_persists_plan_execution_findings_and_events(client, settings) -> None:
    run_id = prepare_run(client)
    provider = MockLLMProvider([PLAN, EXECUTE, COMPLETE])
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, provider, FakeExecutor()
    )

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        tasks = list(session.scalars(select(AnalysisTask).where(AnalysisTask.run_id == run_id)))
        executions = list(session.scalars(select(Execution).where(Execution.run_id == run_id)))
        events = list(
            session.scalars(
                select(RuntimeEvent)
                .where(RuntimeEvent.run_id == run_id)
                .order_by(RuntimeEvent.sequence)
            )
        )
    assert run.status == "completed"
    assert run.state == "DONE"
    assert run.step_count == 4
    assert len(tasks) == 1
    assert tasks[0].status == "completed"
    assert len(executions) == 1
    assert executions[0].script_path == "scripts/001_overview.py"
    event_types = [event.event_type for event in events]
    assert "analysis.plan_created" in event_types
    plan_state_event = next(event for event in events if '"state": "PLAN"' in event.data_json)
    assert plan_state_event.event_type == "analysis.status"
    assert "analysis.execution_started" in event_types
    assert "analysis.execution_completed" in event_types
    assert "analysis.artifact_created" in event_types
    assert event_types[-1] == "analysis.completed"
    from app.services.report_editor_spec import ReportEditorSpec

    assert provider.schemas == [
        AgentActionResponse,
        AgentActionResponse,
        AgentActionResponse,
        ReportEditorSpec,
    ]
    findings_path = settings.workspace_root / run.project_id / "analysis" / "findings.json"
    assert json.loads(findings_path.read_text())["findings"][0]["id"] == "finding_1"


@pytest.mark.asyncio
async def test_clarification_resumes_the_same_run(client, settings) -> None:
    run_id = prepare_run(client, "Clarify run")
    provider = MockLLMProvider(
        [
            {
                "action": "ask_user",
                "question": "Which sales definition?",
                "reason": "Ambiguous field",
            },
            PLAN,
            EXECUTE,
            COMPLETE,
        ]
    )
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, provider, FakeExecutor()
    )

    await orchestrator.run(run_id)
    with client.app.state.database.session() as session:
        assert session.get(AnalysisRun, run_id).status == "waiting_user"
    await orchestrator.resume(run_id, "Use gross sales")

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
    assert run.id == run_id
    assert run.status == "completed"
    assert "User clarification: Use gross sales" in run.user_request


@pytest.mark.asyncio
async def test_failed_code_is_repaired_in_a_new_script(client, settings) -> None:
    run_id = prepare_run(client, "Repair")
    fixed = {**EXECUTE, "filename": "overview_fix.py", "code": "print('fixed')"}
    provider = MockLLMProvider([PLAN, EXECUTE, fixed, COMPLETE])
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        provider,
        FakeExecutor(["failed", "success"]),
    )

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        executions = list(
            session.scalars(
                select(Execution).where(Execution.run_id == run_id).order_by(Execution.created_at)
                )
            )
    assert run.status == "completed"
    assert [execution.status for execution in executions] == ["failed", "success"]
    assert [execution.script_path for execution in executions] == [
        "scripts/001_overview.py",
        "scripts/002_overview_fix.py",
    ]


@pytest.mark.asyncio
async def test_incomplete_dependency_repair_is_rejected_without_spending_retry_budget(
    client, settings
) -> None:
    run_id = prepare_run(client, "Dependency repair preflight")
    incomplete = {
        **EXECUTE,
        "filename": "overview_incomplete.py",
        "code": (
            'fields = ["entity_id"]\n'
            "entities = source[fields].copy()\n"
            'summary = entities.groupby(["group_a", "group_b"]).size()\n'
        ),
    }
    fixed = {**EXECUTE, "filename": "overview_fixed.py", "code": "print('fixed')"}
    provider = MockLLMProvider([PLAN, EXECUTE, incomplete, fixed, COMPLETE])
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        provider,
        FakeExecutor(["failed", "success"]),
    )

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        executions = list(
            session.scalars(
                select(Execution).where(Execution.run_id == run_id).order_by(Execution.created_at)
            )
        )
        failure_events = [
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.python_failure"
        ]

    assert run.status == "completed"
    assert [execution.status for execution in executions] == ["failed", "failed", "success"]
    assert executions[1].script_path == "scripts/002_overview_incomplete.py"
    assert failure_events[1]["docker_executed"] is False
    assert failure_events[1]["failure"]["exception_type"] == "PythonSchemaDependencyError"
    assert [event["retry_count"] for event in failure_events] == [1, 1]


@pytest.mark.asyncio
async def test_preflight_failure_does_not_consume_docker_execution_budget(
    client, settings
) -> None:
    run_id = prepare_run(client, "Preflight repair")
    invalid = {**EXECUTE, "code": 'config = {"enabled": false}'}
    fixed = {**EXECUTE, "filename": "overview_fix.py", "code": "print('fixed')"}
    provider = MockLLMProvider([PLAN, invalid, fixed, COMPLETE])
    executor = FakeExecutor(["success"])
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, provider, executor
    )

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        executions = list(
            session.scalars(
                select(Execution)
                .where(Execution.run_id == run_id)
                .order_by(Execution.created_at)
            )
        )
        completed_events = [
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.execution_completed"
        ]
    assert run.status == "completed"
    assert run.execution_count == 1
    assert [item.status for item in executions] == ["failed", "success"]
    assert completed_events[0]["docker_executed"] is False
    runtime_message = next(
        message
        for message in provider.requests[2]
        if message["content"].startswith("<runtime_state")
    )
    repair_state = json.loads(runtime_message["content"].splitlines()[1])
    assert repair_state["python_repair"]["failed_source"] == invalid["code"]


@pytest.mark.asyncio
async def test_repeated_python_failure_stops_as_stalled_before_retry_limit(
    client, settings
) -> None:
    run_id = prepare_run(client, "Repair stall")
    settings.max_code_repair_stall = 2
    repairs = [
        {**EXECUTE, "filename": f"overview_fix_{index}.py", "code": f"print({index})"}
        for index in range(1, 3)
    ]
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        MockLLMProvider([PLAN, EXECUTE, *repairs]),
        FakeExecutor(["failed", "failed", "failed"]),
    )

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        stopped = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.code_repair_stopped"
        )
    assert run.status == "failed"
    assert run.code_retry_count == 3
    assert run.code_retry_count <= settings.max_code_retry
    assert stopped["mode"] == "stalled"
    assert run.error_message.startswith("Python repair stalled")


def test_python_repair_detects_alternating_failure_oscillation(client, settings) -> None:
    run_id = prepare_run(client, "Repair oscillation")
    settings.max_code_repair_stall = 5
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    with client.app.state.database.session() as session:
        service = AnalysisRunService(session)
        run = service.get(run_id)
        for index, fingerprint in enumerate(["KeyError|date", "NameError|x"] * 2):
            service.event(
                run_id,
                "analysis.python_failure",
                {
                    "failure": {"semantic_fingerprint": fingerprint},
                    "script_fingerprint": f"script-{index}",
                    "artifact_fingerprint_before": "same",
                    "artifact_fingerprint_after": "same",
                },
            )
        transition = orchestrator._python_repair_stop(run)

    assert transition is not None
    assert transition["mode"] == "oscillating"


@pytest.mark.asyncio
async def test_evidence_contract_error_rejects_python_and_requires_declaration(
    client, settings
) -> None:
    run_id = prepare_run(client, "Evidence route")
    executor = FakeExecutor()
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), executor
    )
    with client.app.state.database.session() as session:
        AnalysisRunService(session).event(
            run_id,
            "analysis.artifact_preparation_required",
            {
                "repair_route": "evidence_contract",
                "issues": [{"code": "binding.kpi_unresolvable"}],
            },
        )
    action = AgentActionResponse.model_validate(EXECUTE).root

    assert await orchestrator._execute(run_id, action)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        rejection = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.action_rejected"
        )
    assert run.execution_count == 0
    assert executor.statuses == ["success"]
    assert rejection["reason"] == "evidence_contract_requires_declaration"


@pytest.mark.asyncio
async def test_legacy_report_repair_event_reclassifies_before_python(client, settings) -> None:
    run_id = prepare_run(client, "Legacy evidence route")
    executor = FakeExecutor()
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), executor
    )
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        result_path = settings.workspace_root / run.project_id / "data" / "result.csv"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text("region,sales\nEast,100\n", encoding="utf-8")
        ArtifactService(session).register(
            run.project_id, "data/result.csv", result_path.stat().st_size
        )
    complete = AgentActionResponse.model_validate(COMPLETE).root
    assert orchestrator._complete_analysis(run_id, complete)
    with client.app.state.database.session() as session:
        AnalysisRunService(session).event(
            run_id,
            "analysis.artifact_preparation_required",
            {"issues": [{"code": "legacy.event_without_route"}]},
        )

    action = AgentActionResponse.model_validate(EXECUTE).root
    assert await orchestrator._execute(run_id, action)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        rejection = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.action_rejected"
        )
    assert run.execution_count == 0
    assert executor.statuses == ["success"]
    assert rejection["reason"] == "evidence_contract_requires_declaration"


def test_invalid_evidence_declaration_stall_stops_locally(client, settings) -> None:
    settings.max_report_preparation_attempts = 2
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    run_id = prepare_evidence_repair_run(client, settings, orchestrator)
    action = invalid_evidence_declaration("data/missing.csv").root

    assert orchestrator._declare_report_evidence(run_id, action)
    assert orchestrator._declare_report_evidence(run_id, action)
    assert not orchestrator._declare_report_evidence(run_id, action)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        stopped = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.report_repair_stopped"
        )
    assert run.status == "failed"
    assert stopped["mode"] == "evidence_declaration_stalled"
    assert stopped["transition"]["stall_count"] == 2


def test_changed_evidence_candidate_progresses_to_deeper_validation(client, settings) -> None:
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    run_id = prepare_evidence_repair_run(client, settings, orchestrator)
    schema_error = LLMError(
        "Model repeatedly returned invalid structured output: metrics.0.value: invalid",
        "llm_invalid_output",
        details={
            "schema": "DeclareReportEvidenceAction",
            "validation": "metrics.0.value: Input should be a valid number",
            "candidate_fingerprint": "schema-candidate",
            "finish_reason": "stop",
        },
    )
    assert orchestrator._handle_structured_output_failure(run_id, schema_error)

    action = invalid_evidence_declaration("data/missing.csv").root
    assert orchestrator._declare_report_evidence(run_id, action)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        failures = [
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.evidence_declaration_invalid"
        ]
    transition = failures[-1]["repair_transition"]
    assert transition["classification"] == "progressing"
    assert transition["candidate_changed"] is True
    assert transition["deeper_validation"] is True
    assert transition["stall_count"] == 0


def test_evidence_repair_context_includes_findings_and_previous_candidate(
    client, settings
) -> None:
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    run_id = prepare_evidence_repair_run(client, settings, orchestrator)
    action = invalid_evidence_declaration("data/missing.csv").root

    assert orchestrator._declare_report_evidence(run_id, action)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        context = orchestrator._context(session, run)
        invalid = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.evidence_declaration_invalid"
        )
    feedback = "\n".join(message["content"] for message in context)
    findings_input = next(
        message["content"]
        for message in context
        if message["content"].startswith("<report_evidence_inputs")
    )
    assert '<report_evidence_inputs trust="application-state">' in feedback
    assert '"claim_id": "claim_east"' in feedback
    assert '"risk"' not in findings_input
    assert '"recommendation"' not in findings_input
    assert '<previous_invalid_evidence_candidate trust="untrusted-data">' in feedback
    assert '"source_artifact":"data/missing.csv"' in feedback
    assert "numerator" not in invalid["candidate_manifest"]["metrics"][0]


def test_evidence_repair_context_prefers_current_valid_manifest(
    client, settings
) -> None:
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    run_id = prepare_evidence_repair_run(client, settings, orchestrator)
    valid = AgentActionResponse.model_validate(valid_evidence_declaration()).root
    invalid = invalid_evidence_declaration("data/missing.csv").root

    assert orchestrator._declare_report_evidence(run_id, valid)
    assert orchestrator._declare_report_evidence(run_id, invalid)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        context = orchestrator._context(session, run)
    feedback = "\n".join(message["content"] for message in context)
    current = next(
        message["content"]
        for message in context
        if message["content"].startswith("The current Evidence Manifest")
    )

    assert '<current_valid_evidence_manifest trust="untrusted-data">' in current
    assert '"source_artifact":"data/result.csv"' in current
    assert "data/missing.csv" not in current
    assert "<previous_invalid_evidence_candidate" not in feedback


def test_changed_evidence_candidates_without_deeper_validation_stop_locally(
    client, settings
) -> None:
    settings.max_report_preparation_attempts = 2
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    run_id = prepare_evidence_repair_run(client, settings, orchestrator)

    first = invalid_evidence_declaration("data/missing_a.csv").root
    second = invalid_evidence_declaration("data/missing_b.csv").root
    third = invalid_evidence_declaration("data/missing_c.csv").root

    assert orchestrator._declare_report_evidence(run_id, first)
    assert orchestrator._declare_report_evidence(run_id, second)
    assert not orchestrator._declare_report_evidence(run_id, third)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        failures = [
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.evidence_declaration_invalid"
        ]
        stopped = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.report_repair_stopped"
        )
    assert run.status == "failed"
    assert [item["repair_transition"]["classification"] for item in failures] == [
        "initial",
        "changed",
        "changed",
    ]
    assert [item["repair_transition"]["stall_count"] for item in failures] == [0, 1, 2]
    assert stopped["mode"] == "evidence_declaration_stalled"


def test_invalid_evidence_declaration_oscillation_stops_locally(client, settings) -> None:
    settings.max_report_preparation_attempts = 2
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    run_id = prepare_evidence_repair_run(client, settings, orchestrator)
    first = invalid_evidence_declaration("data/missing_a.csv").root
    second = invalid_evidence_declaration("data/missing_b.csv").root

    assert orchestrator._declare_report_evidence(run_id, first)
    assert orchestrator._declare_report_evidence(run_id, second)
    assert orchestrator._declare_report_evidence(run_id, first)
    assert not orchestrator._declare_report_evidence(run_id, second)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        stopped = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.report_repair_stopped"
        )
    assert run.status == "failed"
    assert stopped["mode"] == "evidence_declaration_oscillating"
    assert stopped["transition"]["oscillation_count"] == 2


@pytest.mark.asyncio
async def test_structured_evidence_error_enters_repair_loop_without_python(
    client, settings
) -> None:
    provider = MockLLMProvider([])
    executor = FakeExecutor()
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, provider, executor
    )
    run_id = prepare_evidence_repair_run(client, settings, orchestrator)
    error = LLMError(
        "Model repeatedly returned invalid structured output: "
        "metrics.0.semantic_type: Input should be 'count'",
        "llm_invalid_output",
        details={
            "schema": "DeclareReportEvidenceAction",
            "validation": "metrics.0.semantic_type: Input should be 'count'",
            "candidate_fingerprint": "candidate-a",
            "finish_reason": "stop",
        },
    )

    assert orchestrator._handle_structured_output_failure(run_id, error)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        context = orchestrator._context(session, run)
        invalid = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.evidence_declaration_invalid"
        )
    assert run.status == "pending"
    assert run.step_count == 0
    assert run.execution_count == 0
    assert executor.statuses == ["success"]
    assert invalid["validation_layer"] == "schema"
    assert invalid["candidate_manifest_fingerprint"] == "candidate-a"
    assert any(
        "semantic_type: Input should be 'count'" in message["content"] for message in context
    )


@pytest.mark.asyncio
async def test_evidence_contract_uses_declaration_only_schema(client, settings) -> None:
    provider = MockLLMProvider([valid_evidence_declaration()])
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, provider, FakeExecutor()
    )
    run_id = prepare_evidence_repair_run(client, settings, orchestrator)

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        declared = any(
            event.event_type == "analysis.report_evidence_declared" for event in run.events
        )
    assert provider.schemas[0] is DeclareReportEvidenceAction
    assert run.execution_count == 0
    assert declared


def test_truncated_evidence_output_enters_same_local_repair_loop(client, settings) -> None:
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    run_id = prepare_evidence_repair_run(client, settings, orchestrator)
    error = LLMError(
        "Model structured output was truncated after repeated attempts",
        "llm_output_truncated",
        details={
            "schema": "AgentActionResponse",
            "validation": "root: Invalid JSON: EOF while parsing",
            "candidate_fingerprint": "candidate-truncated",
            "finish_reason": "length",
        },
    )

    assert orchestrator._handle_structured_output_failure(run_id, error)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        rejected = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.evidence_declaration_invalid"
        )
    assert run.status == "pending"
    assert run.execution_count == 0
    assert rejected["reason"] == "evidence_structured_output_truncated"
    assert rejected["candidate_manifest_fingerprint"] == "candidate-truncated"


@pytest.mark.asyncio
async def test_rejected_report_output_is_repaired_without_reanalysis(client, settings) -> None:
    run_id = prepare_run(client, "Report repair")
    unsafe_html = "<!doctype html><html><script>fetch('https://example.com')</script></html>"
    provider = MockLLMProvider([PLAN, EXECUTE, COMPLETE, unsafe_html])
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, provider, FakeExecutor()
    )

    await orchestrator.run(run_id)
    with client.app.state.database.session() as session:
        completed = session.get(AnalysisRun, run_id)
        execution_count = completed.execution_count
        project_id = completed.project_id
    findings_path = settings.workspace_root / project_id / "analysis" / "findings.json"
    findings_before = findings_path.read_bytes()
    assert completed.status == "completed"
    assert completed.state == "DONE"
    assert completed.execution_count == execution_count
    assert findings_path.read_bytes() == findings_before
    assert (settings.workspace_root / project_id / "reports" / "report.html").is_file()


@pytest.mark.asyncio
async def test_premature_report_action_is_deferred_until_findings_exist(client, settings) -> None:
    run_id = prepare_run(client, "Report gate")
    provider = MockLLMProvider([PLAN, EXECUTE, REPORT, COMPLETE])
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, provider, FakeExecutor()
    )

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        events = list(
            session.scalars(
                select(RuntimeEvent)
                .where(RuntimeEvent.run_id == run_id)
                .order_by(RuntimeEvent.sequence)
            )
        )
    assert run.status == "completed"
    assert run.execution_count == 1
    assert any(event.event_type == "analysis.action_rejected" for event in events)
    retry_context = provider.requests[3]
    assert any("generate_report is not allowed" in message["content"] for message in retry_context)


@pytest.mark.asyncio
async def test_missing_finding_artifact_is_returned_to_agent_for_repair(client, settings) -> None:
    run_id = prepare_run(client, "Missing finding Artifact")
    missing_complete = {
        **COMPLETE,
        "findings": [
            {
                **COMPLETE["findings"][0],
                "related_artifacts": ["data/anomalies.json"],
            }
        ],
    }
    provider = MockLLMProvider(
        [
            PLAN,
            missing_complete,
            {
                "action": "ask_user",
                "question": "Should I regenerate the missing anomaly output?",
                "reason": "A referenced Artifact is missing",
            },
        ]
    )
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, provider, FakeExecutor()
    )

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        events = list(
            session.scalars(
                select(RuntimeEvent)
                .where(RuntimeEvent.run_id == run_id)
                .order_by(RuntimeEvent.sequence)
            )
        )
    assert run.status == "waiting_user"
    rejection = next(
        event
        for event in events
        if event.event_type == "analysis.action_rejected"
        and json.loads(event.data_json).get("reason") == "finding_artifact_missing"
    )
    assert json.loads(rejection.data_json)["missing_artifacts"] == ["data/anomalies.json"]
    retry_context = provider.requests[2]
    feedback = "\n".join(message["content"] for message in retry_context)
    assert "data/anomalies.json" in feedback
    assert "Use execute_python to create the required files" in feedback
    assert "related_artifacts limited to files that actually exist" in feedback


@pytest.mark.asyncio
async def test_agent_step_limit_fails_safely(client, settings) -> None:
    run_id = prepare_run(client, "Limit")
    settings.max_agent_steps = 1
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([PLAN]), FakeExecutor()
    )

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
    assert run.status == "failed"
    assert run.error_message == "Agent step limit reached"


@pytest.mark.asyncio
async def test_execution_limit_fails_before_running_code(client, settings) -> None:
    run_id = prepare_run(client, "Execution limit")
    settings.max_executions_per_run = 0
    executor = FakeExecutor()
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        MockLLMProvider([PLAN, EXECUTE]),
        executor,
    )

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
    assert run.status == "failed"
    assert run.error_message == "Execution limit reached"
    assert executor.statuses == ["success"]


@pytest.mark.asyncio
async def test_code_repair_limit_stops_retries(client, settings) -> None:
    run_id = prepare_run(client, "Repair limit")
    settings.max_code_retry = 0
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        MockLLMProvider([PLAN, EXECUTE]),
        FakeExecutor(["failed"]),
    )

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
    assert run.status == "failed"
    assert run.error_message.startswith("Python repair limit reached")
    assert "Last error: KeyError: sales" in run.error_message
    assert "Script: scripts/001_overview.py" in run.error_message


@pytest.mark.asyncio
async def test_stop_marks_run_and_forwards_to_current_executor(client, settings) -> None:
    run_id = prepare_run(client, "Stop")
    executor = FakeExecutor()
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        run.status = "running"
        run.current_execution_id = "exec_active"
        project_id = run.project_id
    marker = settings.workspace_root / project_id / "data" / "keep.csv"
    marker.write_text("keep")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), executor
    )

    await orchestrator.stop(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
    assert run.status == "stopped"
    assert executor.stopped == ["exec_active"]
    assert marker.read_text() == "keep"


def test_recovery_marks_interrupted_run_failed(client) -> None:
    run_id = prepare_run(client, "Recovery")
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        run.status = "running"
    with client.app.state.database.session() as session:
        assert recover_interrupted_runs(session) == 1
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
    assert run.status == "failed"
    assert "service restart" in run.error_message


def test_complete_analysis_persists_canonical_metric_registry_atomically(client, settings) -> None:
    run_id = prepare_run(client, "Canonical metrics")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    workspace = settings.workspace_root
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        artifact = workspace / run.project_id / "data" / "result.csv"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("region,orders\nEast,100\n", encoding="utf-8")
        ArtifactService(session).register(
            run.project_id, "data/result.csv", artifact.stat().st_size
        )
    action = AgentActionResponse.model_validate(
        {
            "action": "complete_analysis",
            "summary": "Verified order volume",
            "metrics": [
                {
                    "metric_id": "orders",
                    "label": "Orders",
                    "value": 100,
                    "aggregation": "sum",
                    "semantic_type": "count",
                    "unit_family": "count",
                    "count_semantics": "field_sum",
                    "is_distinct": False,
                    "definition": "Sum of orders",
                    "source_artifact": "data/result.csv",
                }
            ],
            "findings": [
                {
                    "id": "finding_orders",
                    "title": "Orders are verified",
                    "evidence": ["data/result.csv contains 100 orders"],
                    "risk": "No immediate risk",
                    "recommendation": "Continue monitoring",
                    "related_artifacts": ["data/result.csv"],
                    "claims": [
                        {
                            "claim_id": "claim_orders",
                            "statement": "Orders equal 100",
                            "priority": "primary",
                            "evidence_metric_ids": ["orders"],
                        }
                    ],
                }
            ],
        }
    ).root
    assert orchestrator._complete_analysis(run_id, action)
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        project_id = run.project_id
    metrics_path = settings.workspace_root / project_id / "analysis" / "metrics.json"
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["metrics"][0]["metric_id"] == "orders"
    assert run.state == "REPORT"


def test_complete_analysis_normalizes_alias_in_large_metric_registry(client, settings) -> None:
    run_id = prepare_run(client, "Large canonical metrics")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        artifact = settings.workspace_root / run.project_id / "data" / "result.csv"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("region,value\nEast,100\n", encoding="utf-8")
        ArtifactService(session).register(
            run.project_id, "data/result.csv", artifact.stat().st_size
        )

    metrics = [
        {
            "metric_id": f"metric_{index}",
            "label": f"Metric {index}",
            "value": index,
            "aggregation": "sum",
            "semantic_type": "measure",
            "unit_family": "quantity",
            "definition": f"Verified value for metric {index}",
            "source_artifact": "data/result.csv",
        }
        for index in range(35)
    ]
    metrics[26]["id"] = metrics[26].pop("metric_id")
    action = AgentActionResponse.model_validate(
        {
            "action": "complete_analysis",
            "summary": "All metrics are verified",
            "metrics": metrics,
            "findings": [
                {
                    "id": "finding_large_registry",
                    "title": "Large registry is available",
                    "evidence": ["data/result.csv contains the verified source values"],
                    "risk": "No immediate risk",
                    "recommendation": "Continue monitoring",
                    "related_artifacts": ["data/result.csv"],
                }
            ],
        }
    ).root

    assert len(action.metrics) == 35
    assert action.metrics[26].metric_id == "metric_26"
    assert orchestrator._complete_analysis(run_id, action)

    with client.app.state.database.session() as session:
        project_id = session.get(AnalysisRun, run_id).project_id
    metrics_path = settings.workspace_root / project_id / "analysis" / "metrics.json"
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))

    assert len(payload["metrics"]) == 35
    assert [item["metric_id"] for item in payload["metrics"]] == [
        f"metric_{index}" for index in range(35)
    ]
    assert all("id" not in item for item in payload["metrics"])



def metric_registry_ratio_action(*, ratio_basis: str) -> AgentActionResponse:
    return AgentActionResponse.model_validate(
        {
            "action": "complete_analysis",
            "summary": "Verified service rate",
            "metrics": [
                {
                    "metric_id": "orders",
                    "label": "Orders",
                    "value": 100,
                    "aggregation": "count",
                    "semantic_type": "count",
                    "unit_family": "count",
                    "count_semantics": "event_count",
                    "is_distinct": False,
                    "definition": "Count of order events",
                    "source_artifact": "data/result.csv",
                },
                {
                    "metric_id": "on_time_events",
                    "label": "On-time order events",
                    "value": 90,
                    "aggregation": "sum",
                    "semantic_type": "measure",
                    "unit_family": "quantity",
                    "definition": "Sum of on-time order events",
                    "source_artifact": "data/result.csv",
                },
                {
                    "metric_id": "on_time_rate",
                    "label": "On-time rate",
                    "value": 0.9,
                    "aggregation": "ratio",
                    "semantic_type": "rate",
                    "unit_family": "percentage",
                    "ratio_basis": ratio_basis,
                    "numerator": "on_time_events",
                    "denominator": "orders",
                    "definition": "on_time_events / orders",
                    "source_artifact": "data/result.csv",
                },
            ],
            "findings": [
                {
                    "id": "finding_service_rate",
                    "title": "Service rate is verified",
                    "evidence": ["data/result.csv contains verified order events"],
                    "risk": "Monitor service quality",
                    "recommendation": "Continue monitoring",
                    "related_artifacts": ["data/result.csv"],
                    "claims": [
                        {
                            "claim_id": "claim_service_rate",
                            "statement": "The on-time rate is 90%",
                            "priority": "primary",
                            "evidence_metric_ids": ["on_time_rate"],
                        }
                    ],
                }
            ],
        }
    )


def test_invalid_metric_registry_enters_contextual_repair(client, settings) -> None:
    run_id = prepare_run(client, "Metric registry recovery")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        artifact = settings.workspace_root / run.project_id / "data" / "result.csv"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("orders,on_time_events\n100,90\n", encoding="utf-8")
        ArtifactService(session).register(
            run.project_id, "data/result.csv", artifact.stat().st_size
        )

    assert orchestrator._complete_analysis(
        run_id, metric_registry_ratio_action(ratio_basis="per_entity").root
    )

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        messages = orchestrator._context(session, run)
        rejection = next(
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.action_rejected"
            and json.loads(event.data_json).get("reason") == "metric_registry_invalid"
        )

    assert run.state == "ANALYZE"
    assert "per_entity ratio is incompatible" in rejection["error"]
    assert any(
        "Metric Registry failed semantic validation" in message["content"]
        and rejection["error"] in message["content"]
        for message in messages
    )


@pytest.mark.asyncio
async def test_generate_report_is_blocked_until_invalid_metric_registry_is_repaired(
    client, settings
) -> None:
    run_id = prepare_run(client, "Metric registry report gate")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        artifact = settings.workspace_root / run.project_id / "data" / "result.csv"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("orders,on_time_events\n100,90\n", encoding="utf-8")
        ArtifactService(session).register(
            run.project_id, "data/result.csv", artifact.stat().st_size
        )

    valid = metric_registry_ratio_action(ratio_basis="per_event").root
    assert orchestrator._complete_analysis(run_id, valid)
    assert orchestrator._complete_analysis(
        run_id, metric_registry_ratio_action(ratio_basis="per_entity").root
    )

    assert await orchestrator._handle_action(
        run_id,
        AgentActionResponse.model_validate(
            {"action": "generate_report", "title": "Service report"}
        ).root,
    )

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        events = [
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.action_rejected"
        ]
        report_started = [
            event for event in run.events if event.event_type == "analysis.report_started"
        ]

    assert run.state == "ANALYZE"
    assert not report_started
    assert events[-1]["action"] == "generate_report"
    assert events[-1]["reason"] == "metric_registry_repair_required"
    assert events[-1]["blocking_reason"] == "metric_registry_invalid"


def test_valid_metric_registry_resolves_prior_invalid_rejection(client, settings) -> None:
    run_id = prepare_run(client, "Metric registry repair completion")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        artifact = settings.workspace_root / run.project_id / "data" / "result.csv"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("orders,on_time_events\n100,90\n", encoding="utf-8")
        ArtifactService(session).register(
            run.project_id, "data/result.csv", artifact.stat().st_size
        )

    assert orchestrator._complete_analysis(
        run_id, metric_registry_ratio_action(ratio_basis="per_entity").root
    )
    assert orchestrator._complete_analysis(
        run_id, metric_registry_ratio_action(ratio_basis="per_event").root
    )

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        assert orchestrator._unresolved_metric_registry_invalid(run) is None
        assert run.state == "REPORT"


def test_complete_analysis_rejects_unsupported_recommendation_parameter(client, settings) -> None:
    run_id = prepare_run(client, "Recommendation provenance")
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), FakeExecutor()
    )
    action = AgentActionResponse.model_validate(
        {
            "action": "complete_analysis",
            "metrics": [],
            "summary": "Retention needs review",
            "findings": [
                {
                    "id": "finding_retention",
                    "title": "Repeat share is low",
                    "evidence": ["Repeat customer share is 2.98%"],
                    "risk": "Retention risk",
                    "recommendation": "建立首购后7/30/90日复购看板",
                    "claims": [],
                }
            ],
        }
    ).root
    assert orchestrator._complete_analysis(run_id, action)
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        rejections = [
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.action_rejected"
        ]
    rejection = rejections[-1]
    assert rejection["reason"] == "unsupported_recommendation_parameter"
    assert [item["parameter"] for item in rejection["issues"]] == ["7", "30", "90"]
    assert not (settings.workspace_root / run.project_id / "analysis" / "findings.json").exists()

