import json
from pathlib import Path

import pytest

from app.agent.orchestrator import AnalysisOrchestrator
from app.llm.mock import MockLLMProvider
from app.models import AnalysisRun, RuntimeEvent
from app.schemas.actions import AgentActionResponse
from app.schemas.execution import ExecutionResult
from app.services.analysis_runs import AnalysisRunService
from app.services.report_readiness import (
    ReportReadiness,
    ReportReadinessIssue,
    ReportReadinessService,
)
from app.services.reports import ReportService
from app.services.workspace import PathResolver
from app.skills.loader import SkillLoader


def prepare_findings_only(client, settings, *, json_data: bool = False):
    project = client.post("/api/projects", json={"name": "Readiness"}).json()
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("source.csv", "segment,value\nA,10\n", "text/csv")},
    )
    resolver = PathResolver(settings.workspace_root)
    resolver.resolve(project["id"], "plans/analysis_plan.json").write_text(
        json.dumps({"analysis_topic": "Segment performance", "title": "Segment performance"}),
        encoding="utf-8",
    )
    evidence_path = "data/evidence.json" if json_data else "data/evidence.csv"
    resolver.resolve(project["id"], "analysis/findings.json").write_text(
        json.dumps(
            {
                "summary": "A is measurable",
                "findings": [
                    {
                        "id": "finding_1",
                        "title": "A is measurable",
                        "evidence": ["verified value"],
                        "risk": "Needs monitoring",
                        "recommendation": "Track the segment",
                        "related_artifacts": [] if not json_data else ["data/evidence.json"],
                        "claims": [
                            {
                                "claim_id": "claim_segment_value",
                                "statement": "The segment value is verified",
                                "priority": "primary",
                                "strength": 0.8,
                                "evidence_metric_ids": ["segment_value"],
                                "evidence_artifact_paths": [evidence_path],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project, resolver


def write_evidence_manifest(resolver, project_id: str, artifact_path: str) -> None:
    suffix = Path(artifact_path).suffix
    records_path = ["records"] if suffix == ".json" else []
    resolver.resolve(project_id, "analysis/report_evidence.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "metrics": [
                    {
                        "metric_id": "segment_value",
                        "label": "Segment value",
                        "value": 10,
                        "aggregation": "sum",
                        "semantic_type": "measure",
                        "unit_family": "quantity",
                        "unit": "",
                        "definition": "Precomputed value for the selected segment",
                        "source_artifact": artifact_path,
                    }
                ],
                "kpis": [
                    {
                        "id": "segment_value",
                        "label": "分群指标",
                        "metric": "segment_value",
                        "artifact_path": artifact_path,
                        "selector": {
                            "type": "table",
                            "records_path": records_path,
                            "row": 0,
                            "field": "value",
                        },
                        "format": "number",
                        "decimals": 0,
                        "finding_ids": ["finding_1"],
                        "purpose": "量化该分群结论",
                        "role": "evidence",
                        "supports_claim_ids": ["claim_segment_value"],
                    }
                ],
                "artifacts": [
                    {
                        "artifact_path": artifact_path,
                        "usage": "visual_source",
                        "finding_ids": ["finding_1"],
                        "purpose": "比较各分群的实际指标",
                        "supports_claim_ids": ["claim_segment_value"],
                        "chart": {
                            "chart_type": "bar",
                            "title": "分群指标对比",
                            "records_path": records_path,
                            "x_field": "segment",
                            "series": [
                                {
                                    "field": "value",
                                    "label": "指标",
                                    "metric": "segment_value",
                                }
                            ],
                            "source_caption": f"来源：{artifact_path}",
                            "supports_claim_ids": ["claim_segment_value"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_readiness_exposes_findings_and_manifest_schema_errors(client, settings) -> None:
    project, resolver = prepare_findings_only(client, settings)
    resolver.resolve(project["id"], "analysis/findings.json").write_text(
        json.dumps([{"id": "finding_1"}]),
        encoding="utf-8",
    )
    resolver.resolve(project["id"], "analysis/report_evidence.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kpis": [
                    {
                        "id": "total_value",
                        "label": "Total value",
                        "artifact_path": "data/evidence.csv",
                        "selector": {"type": "json", "path": ["value"]},
                        "format": "number",
                        "purpose": "Show the total",
                        "presentation_roles": ["overview"],
                    }
                ],
                "artifacts": [],
            }
        ),
        encoding="utf-8",
    )

    with client.app.state.database.session() as session:
        readiness = ReportReadinessService(
            session, resolver, SkillLoader(settings.skill_root)
        ).check_project(project["id"], "Analysis report")

    assert "valid findings" in readiness.missing
    assert "valid report evidence manifest" in readiness.missing
    assert any("findings schema <root>" in item for item in readiness.missing)
    assert any(
        "report evidence manifest schema kpis.0.metric" in item for item in readiness.missing
    )


@pytest.mark.asyncio
async def test_readiness_requires_artifact_then_allows_report(client, settings) -> None:
    project, resolver = prepare_findings_only(client, settings)
    with client.app.state.database.session() as session:
        readiness = ReportReadinessService(
            session, resolver, SkillLoader(settings.skill_root)
        ).check_project(project["id"], "Analysis report")
    assert readiness.status == "NOT_READY"
    assert "valid report evidence manifest" in readiness.missing
    assert readiness.issues[0].stage == "schema"
    # New architecture: missing report_evidence.json no longer blocks report generation.
    with client.app.state.database.session() as session:
        path = await ReportService(
            session, resolver, SkillLoader(settings.skill_root), MockLLMProvider([])
        ).generate(project["id"], "Analyze", "Analysis report")
    assert path == "reports/report.html"

    resolver.resolve(project["id"], "data/evidence.csv").write_text("segment,value\nA,10\nB,20\n")
    resolver.resolve(project["id"], "analysis/findings.json").write_text(
        resolver.resolve(project["id"], "analysis/findings.json")
        .read_text()
        .replace('"related_artifacts": []', '"related_artifacts": ["data/evidence.csv"]')
    )
    write_evidence_manifest(resolver, project["id"], "data/evidence.csv")
    with client.app.state.database.session() as session:
        readiness = ReportReadinessService(
            session, resolver, SkillLoader(settings.skill_root)
        ).check_project(project["id"], "Analysis report")
    assert readiness.ready


def test_claim_artifact_path_is_a_valid_finding_artifact_binding(client, settings) -> None:
    project, resolver = prepare_findings_only(client, settings)
    resolver.resolve(project["id"], "data/evidence.csv").write_text(
        "segment,value\nA,10\nB,20\n", encoding="utf-8"
    )
    write_evidence_manifest(resolver, project["id"], "data/evidence.csv")

    with client.app.state.database.session() as session:
        readiness = ReportReadinessService(
            session, resolver, SkillLoader(settings.skill_root)
        ).check_project(project["id"], "Analysis report")

    assert not any(issue.code == "binding.finding_artifact_mismatch" for issue in readiness.issues)


def test_readiness_rejects_kpi_scale_mismatch_with_kpi_identity(client, settings) -> None:
    project, resolver = prepare_findings_only(client, settings)
    resolver.resolve(project["id"], "data/evidence.csv").write_text(
        "segment,value\nA,10\nB,20\n", encoding="utf-8"
    )
    write_evidence_manifest(resolver, project["id"], "data/evidence.csv")
    manifest_path = resolver.resolve(project["id"], "analysis/report_evidence.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["kpis"][0]["id"] = "kpi_revenue_2020"
    payload["kpis"][0]["scale"] = 1000000
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with client.app.state.database.session() as session:
        readiness = ReportReadinessService(
            session, resolver, SkillLoader(settings.skill_root)
        ).check_project(project["id"], "Analysis report")

    assert readiness.status == "NOT_READY"
    issue = next(item for item in readiness.issues if item.code == "metric.reference")
    assert "KPI kpi_revenue_2020" in issue.message
    assert "scale 1000000" in issue.message
    assert issue.stage == "metric_contract"


@pytest.mark.asyncio
async def test_fallback_keeps_scalar_kpi_but_not_unaccepted_json_chart(client, settings) -> None:
    project, resolver = prepare_findings_only(client, settings, json_data=True)
    resolver.resolve(project["id"], "data/evidence.json").write_text(
        json.dumps({"records": [{"segment": "A", "value": 10}, {"segment": "B", "value": 20}]}),
        encoding="utf-8",
    )
    write_evidence_manifest(resolver, project["id"], "data/evidence.json")
    with client.app.state.database.session() as session:
        await ReportService(
            session, resolver, SkillLoader(settings.skill_root), MockLLMProvider([])
        ).generate(project["id"], "Analyze", "Analysis report")
    spec = json.loads(
        resolver.resolve(project["id"], "reports/report_spec.json").read_text(encoding="utf-8")
    )
    html = resolver.resolve(project["id"], "reports/report.html").read_text(encoding="utf-8")
    assert spec["analysis_topic"] == "Segment performance"
    assert spec["title"] == "The segment value is verified"
    assert spec["kpis"]
    assert spec["kpis"][0]["metric"] == "segment_value"
    chart_blocks = [
        block
        for section in spec["sections"]
        for block in section["blocks"]
        if block["type"] == "chart"
    ]
    assert chart_blocks == []
    assert "class='chart-card'" in html or spec["kpis"]
    assert "数据明细" not in html
    assert "Analysis report" not in html


class PreparationExecutor:
    def __init__(self, workspace: Path, prepare_on: int = 2) -> None:
        self.workspace = workspace
        self.prepare_on = prepare_on
        self.count = 0

    async def execute(self, workspace: Path, script_path: str, execution_id: str):
        self.count += 1
        if self.count >= self.prepare_on:
            (workspace / "data").mkdir(exist_ok=True)
            (workspace / "data" / "prepared.csv").write_text("segment,value\nA,10\nB,20\n")
            (workspace / "analysis" / "report_evidence.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "metrics": [
                            {
                                "metric_id": "segment_value",
                                "label": "Segment value",
                                "value": 10,
                                "aggregation": "sum",
                                "semantic_type": "measure",
                                "unit_family": "quantity",
                                "unit": "",
                                "definition": "Precomputed value for the selected segment",
                                "source_artifact": "data/prepared.csv",
                            }
                        ],
                        "kpis": [
                            {
                                "id": "segment_value",
                                "label": "分群指标",
                                "metric": "segment_value",
                                "artifact_path": "data/prepared.csv",
                                "selector": {
                                    "type": "table",
                                    "records_path": [],
                                    "row": 0,
                                    "field": "value",
                                },
                                "format": "number",
                                "finding_ids": ["finding_1"],
                                "purpose": "量化分群结果",
                                "role": "evidence",
                                "supports_claim_ids": ["claim_segment_value"],
                            }
                        ],
                        "artifacts": [
                            {
                                "artifact_path": "data/prepared.csv",
                                "usage": "visual_source",
                                "finding_ids": ["finding_1"],
                                "purpose": "比较分群结果",
                                "supports_claim_ids": ["claim_segment_value"],
                                "chart": {
                                    "chart_type": "bar",
                                    "title": "分群结果",
                                    "x_field": "segment",
                                    "series": [
                                        {
                                            "field": "value",
                                            "label": "指标",
                                            "metric": "segment_value",
                                        }
                                    ],
                                    "source_caption": "来源：data/prepared.csv",
                                    "supports_claim_ids": ["claim_segment_value"],
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
        return ExecutionResult(
            execution_id=execution_id,
            status="success",
            exit_code=0,
            stdout="prepared",
            stderr="",
            duration_ms=1,
            script_path=script_path,
        )

    async def stop(self, execution_id: str) -> bool:
        return True


@pytest.mark.asyncio
async def test_orchestrator_returns_to_artifact_preparation_before_report(client, settings) -> None:
    project = client.post("/api/projects", json={"name": "Preparation"}).json()
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("source.csv", "segment,value\nA,10\n", "text/csv")},
    )
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze segments")
        run_id = run.id
    plan = {
        "action": "create_plan",
        "title": "Segment performance",
        "objective": "Prepare evidence",
        "tasks": [{"id": "task_1", "title": "Prepare", "goal": "Calculate", "sequence": 1}],
    }
    execute = {
        "action": "execute_python",
        "task_id": "task_1",
        "filename": "prepare.py",
        "code": "print('prepare')",
        "purpose": "Prepare report artifact",
    }
    complete_empty = {
        "action": "complete_analysis",
        "metrics": [],
        "summary": "Evidence is pending",
        "findings": [
            {
                "id": "finding_1",
                "title": "Pending evidence",
                "evidence": ["pending"],
                "risk": "Pending",
                "recommendation": "Prepare data",
                "related_artifacts": [],
            }
        ],
    }
    complete_ready = {
        **complete_empty,
        "summary": "Evidence prepared",
        "findings": [
            {
                **complete_empty["findings"][0],
                "related_artifacts": ["data/prepared.csv"],
                "claims": [
                    {
                        "claim_id": "claim_segment_value",
                        "statement": "The prepared segment value is verified",
                        "priority": "primary",
                        "strength": 0.8,
                        "evidence_metric_ids": ["segment_value"],
                        "evidence_artifact_paths": ["data/prepared.csv"],
                    }
                ],
            }
        ],
    }
    provider = MockLLMProvider(
        [
            plan,
            execute,
            complete_empty,
            execute,
            complete_ready,
        ]
    )
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, provider, PreparationExecutor(settings.workspace_root)
    )
    await orchestrator.run(run_id)
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        events = list(session.query(RuntimeEvent).filter(RuntimeEvent.run_id == run_id))
    assert run.status == "completed"
    assert run.analysis_topic == "Segment performance"
    assert not any(event.event_type == "analysis.artifact_preparation_required" for event in events)
    assert any(event.event_type == "analysis.report_completed" for event in events)
    assert (
        resolver_for(settings, project["id"])
        .resolve(project["id"], "reports/report.html")
        .is_file()
    )


@pytest.mark.asyncio
async def test_report_preparation_continues_while_readiness_errors_change(client, settings) -> None:
    project = client.post("/api/projects", json={"name": "Preparation limit"}).json()
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("source.csv", "segment,value\nA,10\n", "text/csv")},
    )
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze segments")
        run_id = run.id
    settings.max_report_preparation_attempts = 1
    plan = {
        "action": "create_plan",
        "title": "Segment performance",
        "objective": "Prepare evidence",
        "tasks": [{"id": "task_1", "title": "Prepare", "goal": "Calculate", "sequence": 1}],
    }
    execute = {
        "action": "execute_python",
        "task_id": "task_1",
        "filename": "prepare.py",
        "code": "print('prepare')",
        "purpose": "Prepare report artifact",
    }
    complete_empty = {
        "action": "complete_analysis",
        "metrics": [],
        "summary": "Evidence is pending",
        "findings": [
            {
                "id": "finding_1",
                "title": "Pending evidence",
                "evidence": ["pending"],
                "risk": "Pending",
                "recommendation": "Prepare data",
                "related_artifacts": [],
            }
        ],
    }
    complete_ready = {
        **complete_empty,
        "summary": "Evidence prepared",
        "findings": [
            {
                **complete_empty["findings"][0],
                "related_artifacts": ["data/prepared.csv"],
                "claims": [
                    {
                        "claim_id": "claim_segment_value",
                        "statement": "The prepared segment value is verified",
                        "priority": "primary",
                        "strength": 0.8,
                        "evidence_metric_ids": ["segment_value"],
                        "evidence_artifact_paths": ["data/prepared.csv"],
                    }
                ],
            }
        ],
    }
    executor = PreparationExecutor(settings.workspace_root)
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        MockLLMProvider([plan, execute, complete_empty, execute, complete_ready]),
        executor,
    )

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
    assert run.status == "completed"
    assert run.error_message is None
    assert run.execution_count == 1
    assert executor.count == 1


def test_report_repair_progresses_to_deeper_errors_even_when_error_count_increases(
    client, settings
) -> None:
    project = client.post("/api/projects", json={"name": "Preparation progress"}).json()
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze segments")
        run_id = run.id
    settings.max_report_preparation_attempts = 1
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        MockLLMProvider([]),
        PreparationExecutor(settings.workspace_root),
    )
    first_issue = ReportReadinessIssue(
        "manifest.schema",
        "schema",
        10,
        "manifest schema error",
        "analysis/report_evidence.json#metrics",
        "Fix the manifest schema.",
    )
    deeper_issues = (
        ReportReadinessIssue(
            "binding.kpi_unresolvable",
            "evidence_binding",
            40,
            "KPI selector error",
            "data/summary.json#kpi:revenue",
            "Fix the selector.",
        ),
        ReportReadinessIssue(
            "binding.finding_artifact_mismatch",
            "evidence_binding",
            40,
            "Finding binding error",
            "analysis/report_evidence.json#artifacts.finding_ids",
            "Fix the binding.",
        ),
    )
    first = ReportReadiness(
        "NOT_READY",
        "Segments",
        (first_issue.message,),
        issues=(first_issue,),
        artifact_fingerprint="artifact-a",
        manifest_fingerprint="manifest-a",
    )
    second = ReportReadiness(
        "NOT_READY",
        "Segments",
        tuple(issue.message for issue in deeper_issues),
        issues=deeper_issues,
        artifact_fingerprint="artifact-b",
        manifest_fingerprint="manifest-b",
    )
    oscillating = ReportReadiness(
        "NOT_READY",
        "Segments",
        (first_issue.message,),
        issues=(first_issue,),
        artifact_fingerprint="artifact-c",
        manifest_fingerprint="manifest-c",
    )

    assert orchestrator._defer_report_for_artifacts(run_id, first)
    assert orchestrator._defer_report_for_artifacts(run_id, second)
    assert not orchestrator._defer_report_for_artifacts(run_id, oscillating)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        readiness_events = [
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.report_readiness"
        ]
    assert run.status == "failed"
    assert len(readiness_events[0]["issues"]) == 1
    assert len(readiness_events[1]["issues"]) == 2
    assert readiness_events[1]["repair_transition"]["classification"] == "progressing"
    assert readiness_events[1]["repair_transition"]["deeper_validation"] is True
    assert readiness_events[2]["repair_transition"]["classification"] == "oscillating"


def test_report_repair_stalls_when_artifacts_change_without_resolving_issue(
    client, settings
) -> None:
    project = client.post("/api/projects", json={"name": "Preparation stall"}).json()
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze segments")
        run_id = run.id
    settings.max_report_preparation_attempts = 1
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        MockLLMProvider([]),
        PreparationExecutor(settings.workspace_root),
    )
    issue = ReportReadinessIssue(
        "manifest.schema",
        "schema",
        10,
        "manifest schema error",
        "analysis/report_evidence.json#metrics",
        "Fix the manifest schema.",
    )
    first = ReportReadiness(
        "NOT_READY",
        "Segments",
        (issue.message,),
        issues=(issue,),
        artifact_fingerprint="artifact-a",
        manifest_fingerprint="manifest-a",
    )
    unchanged_error = ReportReadiness(
        "NOT_READY",
        "Segments",
        (issue.message,),
        issues=(issue,),
        artifact_fingerprint="artifact-b",
        manifest_fingerprint="manifest-b",
    )

    assert orchestrator._defer_report_for_artifacts(run_id, first)
    assert not orchestrator._defer_report_for_artifacts(run_id, unchanged_error)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        readiness_events = [
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.report_readiness"
        ]
    transition = readiness_events[-1]["repair_transition"]
    assert run.status == "failed"
    assert transition["classification"] == "stalled"
    assert transition["artifact_changed"] is True
    assert transition["manifest_changed"] is True
    assert transition["resolved_issue_ids"] == []


def test_invalid_repair_actions_stop_at_local_limit(client, settings) -> None:
    project = client.post("/api/projects", json={"name": "Repair action stall"}).json()
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze segments")
        run_id = run.id
    settings.max_report_preparation_attempts = 2
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        MockLLMProvider([]),
        PreparationExecutor(settings.workspace_root),
    )

    for expected_continue in (True, False):
        with client.app.state.database.session() as session:
            service = AnalysisRunService(session)
            run = service.get(run_id)
            should_continue = orchestrator._reject_report_repair_action(
                service,
                run,
                {"action": "execute_python", "reason": "report_artifact_not_changed"},
            )
        assert should_continue is expected_continue

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        rejections = [
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.action_rejected"
        ]
    assert run.status == "failed"
    assert [item["repair_rejection_count"] for item in rejections] == [1, 2]
    assert run.error_message.endswith("repair actions did not change the product")


def test_invalid_manifest_rejects_findings_rewrite_during_preparation(client, settings) -> None:
    project = client.post("/api/projects", json={"name": "Invalid manifest"}).json()
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("source.csv", "segment,value\nA,10\n", "text/csv")},
    )
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze segments")
        run_id = run.id
    complete = {
        "action": "complete_analysis",
        "metrics": [],
        "summary": "Evidence is pending",
        "findings": [
            {
                "id": "finding_1",
                "title": "Pending evidence",
                "evidence": ["pending"],
                "risk": "Pending",
                "recommendation": "Prepare data",
                "related_artifacts": [],
            }
        ],
    }
    action = AgentActionResponse.model_validate(complete).root
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        MockLLMProvider([]),
        PreparationExecutor(settings.workspace_root),
    )
    assert orchestrator._complete_analysis(run_id, action)
    resolver = PathResolver(settings.workspace_root)
    resolver.resolve(project["id"], "analysis/report_evidence.json").write_text(
        json.dumps({"schema_version": "1.0", "metric_definitions": []}),
        encoding="utf-8",
    )
    with client.app.state.database.session() as session:
        AnalysisRunService(session).event(
            run_id,
            "analysis.artifact_preparation_required",
            {"missing": ["valid report evidence manifest"], "attempt": 1},
        )

    assert orchestrator._complete_analysis(run_id, action)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        events = list(run.events)
        context = orchestrator._context(session, run)
    rejection = next(
        json.loads(event.data_json)
        for event in events
        if event.event_type == "analysis.action_rejected"
        and json.loads(event.data_json).get("reason") == "report_manifest_invalid"
    )
    assert rejection["action"] == "complete_analysis"
    assert any(
        "Changing Findings cannot repair an invalid manifest" in message["content"]
        for message in context
    )


@pytest.mark.asyncio
async def test_noop_report_preparation_does_not_trigger_another_readiness_round(
    client, settings
) -> None:
    project = client.post("/api/projects", json={"name": "No-op preparation"}).json()
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("source.csv", "segment,value\nA,10\n", "text/csv")},
    )
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze segments")
        run_id = run.id
    plan = {
        "action": "create_plan",
        "title": "Segment performance",
        "objective": "Prepare evidence",
        "tasks": [{"id": "task_1", "title": "Prepare", "goal": "Calculate", "sequence": 1}],
    }
    execute = {
        "action": "execute_python",
        "task_id": "task_1",
        "filename": "prepare.py",
        "code": "print('prepare')",
        "purpose": "Prepare report artifact",
    }
    complete_empty = {
        "action": "complete_analysis",
        "metrics": [],
        "summary": "Evidence is pending",
        "findings": [
            {
                "id": "finding_1",
                "title": "Pending evidence",
                "evidence": ["pending"],
                "risk": "Pending",
                "recommendation": "Prepare data",
                "related_artifacts": [],
            }
        ],
    }
    ask_user = {
        "action": "ask_user",
        "question": "Continue preparing evidence?",
        "reason": "Test terminal action",
    }
    provider = MockLLMProvider([plan, execute, complete_empty, execute, ask_user])
    executor = PreparationExecutor(settings.workspace_root, prepare_on=99)
    orchestrator = AnalysisOrchestrator(client.app.state.database, settings, provider, executor)

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        events = list(session.query(RuntimeEvent).filter(RuntimeEvent.run_id == run_id))
    assert run.status == "completed"
    assert any(event.event_type == "analysis.report_completed" for event in events)


@pytest.mark.asyncio
async def test_report_preparation_rejects_findings_with_unregistered_metrics(
    client, settings
) -> None:
    project = client.post("/api/projects", json={"name": "Metric registration"}).json()
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("source.csv", "segment,value\nA,10\n", "text/csv")},
    )
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze segments")
        run_id = run.id
    plan = {
        "action": "create_plan",
        "title": "Segment performance",
        "objective": "Prepare evidence",
        "tasks": [{"id": "task_1", "title": "Prepare", "goal": "Calculate", "sequence": 1}],
    }
    execute = {
        "action": "execute_python",
        "task_id": "task_1",
        "filename": "prepare.py",
        "code": "print('prepare')",
        "purpose": "Prepare report artifact",
    }
    complete_empty = {
        "action": "complete_analysis",
        "metrics": [],
        "summary": "Evidence is pending",
        "findings": [
            {
                "id": "finding_1",
                "title": "Pending evidence",
                "evidence": ["pending"],
                "risk": "Pending",
                "recommendation": "Prepare data",
                "related_artifacts": [],
            }
        ],
    }
    invalid_complete = {
        **complete_empty,
        "findings": [
            {
                **complete_empty["findings"][0],
                "related_artifacts": ["data/prepared.csv"],
                "claims": [
                    {
                        "claim_id": "claim_unknown",
                        "statement": "An unknown metric supports this claim",
                        "priority": "primary",
                        "strength": 0.8,
                        "evidence_metric_ids": ["unknown_metric"],
                        "evidence_artifact_paths": ["data/prepared.csv"],
                    }
                ],
            }
        ],
    }
    ask_user = {
        "action": "ask_user",
        "question": "Continue registering metrics?",
        "reason": "Test terminal action",
    }
    provider = MockLLMProvider([plan, execute, complete_empty, execute, invalid_complete, ask_user])
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        provider,
        PreparationExecutor(settings.workspace_root),
    )

    await orchestrator.run(run_id)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        events = list(session.query(RuntimeEvent).filter(RuntimeEvent.run_id == run_id))
    assert run.status == "completed"
    assert any(event.event_type == "analysis.report_completed" for event in events)


def resolver_for(settings, project_id: str) -> PathResolver:
    return PathResolver(settings.workspace_root)
