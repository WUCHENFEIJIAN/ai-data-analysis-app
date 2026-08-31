import json

import pytest
from sqlalchemy import select

from app.agent.orchestrator import AnalysisOrchestrator
from app.core.errors import REPORT_ERROR_MESSAGES, ReportPipelineError
from app.llm.mock import MockLLMProvider
from app.models import AnalysisRun, RuntimeEvent
from app.services.analysis_runs import AnalysisRunService
from app.services.report_fallback import FallbackSpecBuilder
from app.services.report_inputs import ReportInputCollector
from app.services.report_renderer import ReportRenderer
from app.services.reports import ReportService
from app.services.workspace import PathResolver
from app.skills.loader import SkillLoader
from tests.test_orchestrator import FakeExecutor
from tests.test_report_editor_pipeline import (
    _callout_with_claim_ids,
    _editor_spec,
    _generate,
    prepare_editor_project,
)


def _html(resolver: PathResolver, project_id: str) -> str:
    return resolver.resolve(project_id, "reports/report.html").read_text(encoding="utf-8")


def _spec_text(resolver: PathResolver, project_id: str) -> str:
    return resolver.resolve(project_id, "reports/report_spec.json").read_text(encoding="utf-8")

OLD_HTML = "<!doctype html><html><body><h1>OLD REPORT</h1></body></html>"


def _write_old_report(resolver: PathResolver, project_id: str) -> None:
    html_path = resolver.resolve(project_id, "reports/report.html")
    spec_path = resolver.resolve(project_id, "reports/report_spec.json")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(OLD_HTML, encoding="utf-8")
    spec_path.write_text('{"title": "old"}', encoding="utf-8")


def test_fallback_only_binds_existing_claims(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver).collect(
            project["id"], "Analyze", "Category performance"
        )
    spec = FallbackSpecBuilder(resolver).build(project["id"], inputs, None)
    bound = []
    for section in spec.sections:
        bound.extend(section.claim_ids)
        for block in section.blocks:
            bound.extend(getattr(block, "claim_ids", []) or [])
            if getattr(block, "items", None):
                for item in block.items:
                    bound.extend(getattr(item, "source_claim_ids", []) or [])
    assert bound
    assert set(bound) <= {"claim_total"}
    assert spec.provenance.planner_mode == "fallback"


@pytest.mark.asyncio
async def test_llm_unavailable_uses_fallback(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    path = await _generate(client, settings, project, resolver, MockLLMProvider([]))
    spec = json.loads(
        resolver.resolve(project["id"], "reports/report_spec.json").read_text(encoding="utf-8")
    )
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert spec["provenance"]["planner_mode"] == "fallback"
    assert html.startswith("<!doctype html>")


@pytest.mark.asyncio
async def test_editor_retries_exhausted_uses_fallback(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    path = await _generate(
        client,
        settings,
        project,
        resolver,
        MockLLMProvider([_callout_with_claim_ids(), _callout_with_claim_ids()]),
    )
    spec = json.loads(
        resolver.resolve(project["id"], "reports/report_spec.json").read_text(encoding="utf-8")
    )
    assert spec["provenance"]["planner_mode"] == "fallback"
    assert resolver.resolve(project["id"], path).is_file()


@pytest.mark.asyncio
async def test_renderer_failure_does_not_overwrite_old_report(
    client, settings, monkeypatch
) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    _write_old_report(resolver, project["id"])

    def boom(self, project_id: str, spec) -> str:
        raise RuntimeError("synthetic renderer crash")

    monkeypatch.setattr(ReportRenderer, "render", boom)
    with client.app.state.database.session() as session:
        with pytest.raises(ReportPipelineError) as caught:
            await ReportService(
                session,
                resolver,
                SkillLoader(settings.skill_root),
                MockLLMProvider([_editor_spec()]),
            ).generate(project["id"], "Analyze", "Category performance")
    assert caught.value.code == "report_render_failed"
    assert _html(resolver, project["id"]) == OLD_HTML
    assert _spec_text(resolver, project["id"]) == '{"title": "old"}'


@pytest.mark.asyncio
async def test_publish_failure_does_not_overwrite_old_report(
    client, settings, monkeypatch
) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    _write_old_report(resolver, project["id"])

    def boom_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("app.services.reports.os.replace", boom_replace)
    with client.app.state.database.session() as session:
        with pytest.raises(ReportPipelineError) as caught:
            await ReportService(
                session,
                resolver,
                SkillLoader(settings.skill_root),
                MockLLMProvider([_editor_spec()]),
            ).generate(project["id"], "Analyze", "Category performance")
    assert caught.value.code == "report_publish_failed"
    assert _html(resolver, project["id"]) == OLD_HTML


@pytest.mark.asyncio
async def test_orchestrator_publish_failure_marks_run_failed_not_completed(
    client, settings, monkeypatch
) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    _write_old_report(resolver, project["id"])
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze")
        run.state = "REPORT"
        run_id = run.id

    def boom_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("app.services.reports.os.replace", boom_replace)
    orchestrator = AnalysisOrchestrator(
        client.app.state.database,
        settings,
        MockLLMProvider([_editor_spec()]),
        FakeExecutor(),
    )
    await orchestrator.run(run_id)
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        events = session.scalars(
            select(RuntimeEvent).where(RuntimeEvent.run_id == run_id)
        ).all()
    payload = json.loads(
        next(event.data_json for event in events if event.event_type == "analysis.failed")
    )
    assert run.status == "failed"
    assert run.state == "REPORT"
    assert run.error_message == REPORT_ERROR_MESSAGES["report_publish_failed"]
    assert payload["error_code"] == "report_publish_failed"
    assert _html(resolver, project["id"]) == OLD_HTML
