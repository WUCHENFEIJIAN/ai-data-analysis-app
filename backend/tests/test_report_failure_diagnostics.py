import json
import logging
import sys

import pytest
from sqlalchemy import select

from app.agent.orchestrator import AnalysisOrchestrator
from app.core.errors import REPORT_ERROR_MESSAGES, ReportPipelineError, sanitize_diagnostics
from app.core.logging import JsonFormatter
from app.llm.mock import MockLLMProvider
from app.models import AnalysisRun, RuntimeEvent
from app.services.analysis_runs import AnalysisRunService
from app.services.reports import ReportRenderer, ReportService
from app.skills.loader import SkillLoader
from tests.test_orchestrator import FakeExecutor
from tests.test_report_editor_pipeline import _editor_spec, prepare_editor_project


def test_json_formatter_includes_traceback_and_context_without_secrets() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="app.agent.orchestrator",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="analysis_failed run_id=%s code=%s",
        args=("run_1", "report_render_failed"),
        exc_info=None,
    )
    try:
        raise RuntimeError("synthetic renderer crash")
    except RuntimeError:
        record.exc_info = sys.exc_info()
    record.run_id = "run_1"
    record.project_id = "pj_1"
    record.stage = "REPORT"
    record.error_code = "report_render_failed"
    record.api_key = "sk-secret-value"
    record.prompt = "FULL PROMPT WITH USER DATA"
    payload = json.loads(formatter.format(record))
    raw = formatter.format(record)
    assert payload["exception_type"] == "RuntimeError"
    assert "Traceback (most recent call last)" in payload["traceback"]
    assert "synthetic renderer crash" in payload["traceback"]
    assert payload["run_id"] == "run_1"
    assert payload["project_id"] == "pj_1"
    assert payload["stage"] == "REPORT"
    assert payload["error_code"] == "report_render_failed"
    assert "sk-secret-value" not in raw
    assert "FULL PROMPT WITH USER DATA" not in raw
    assert "api_key" not in payload
    assert "prompt" not in payload


def test_sanitize_diagnostics_strips_secrets_and_prompts() -> None:
    cleaned = sanitize_diagnostics(
        {
            "exception_type": "RuntimeError",
            "api_key": "sk-secret-value",
            "prompt": "Return one complete JSON object",
            "messages": [{"role": "system", "content": "secret system prompt"}],
            "section": "section_1",
            "nested": {"authorization": "Bearer xyz", "block": "narrative"},
        }
    )
    assert cleaned["exception_type"] == "RuntimeError"
    assert cleaned["section"] == "section_1"
    assert cleaned["nested"] == {"block": "narrative"}
    assert "api_key" not in cleaned
    assert "prompt" not in cleaned
    assert "messages" not in cleaned
    dumped = json.dumps(cleaned)
    assert "sk-secret-value" not in dumped
    assert "secret system prompt" not in dumped
    assert "Bearer xyz" not in dumped


@pytest.mark.asyncio
async def test_renderer_exception_is_recorded_as_report_render_failed(
    client, settings, monkeypatch, caplog
) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze")
        run.state = "REPORT"
        run_id = run.id

    def boom(self, project_id: str, spec) -> str:
        raise RuntimeError("synthetic renderer crash")

    monkeypatch.setattr(ReportRenderer, "render", boom)
    caplog.set_level(logging.ERROR)
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
            select(RuntimeEvent)
            .where(RuntimeEvent.run_id == run_id)
            .order_by(RuntimeEvent.sequence)
        ).all()

    failed = next(event for event in events if event.event_type == "analysis.failed")
    payload = json.loads(failed.data_json)
    assert run.status == "failed"
    assert run.state == "REPORT"
    assert run.error_message == REPORT_ERROR_MESSAGES["report_render_failed"]
    assert "Traceback" not in run.error_message
    assert "synthetic renderer crash" not in run.error_message
    assert payload["error_code"] == "report_render_failed"
    assert payload["stage"] == "REPORT"
    assert payload["diagnostics"]["exception_type"] == "RuntimeError"
    assert "api_key" not in json.dumps(payload)
    assert "prompt" not in json.dumps(payload).lower()
    assert "Traceback (most recent call last)" in caplog.text
    assert "synthetic renderer crash" in caplog.text
    assert "report_render_failed" in caplog.text


@pytest.mark.asyncio
async def test_report_service_wraps_renderer_crash(client, settings, monkeypatch) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)

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
    assert caught.value.stage == "REPORT"
    assert "synthetic renderer crash" not in caught.value.message
