import asyncio
import json
from threading import Event

from app.agent.orchestrator import AnalysisOrchestrator
from app.llm.mock import MockLLMProvider
from app.models import AnalysisRun
from app.services.analysis_runs import AnalysisRunService


def test_latest_analysis_restores_only_the_requested_project_run(client) -> None:
    first_project = client.post("/api/projects", json={"name": "First"}).json()
    second_project = client.post("/api/projects", json={"name": "Second"}).json()

    assert client.get(f"/api/projects/{first_project['id']}/analysis").json() is None
    with client.app.state.database.session() as session:
        first_run, _ = AnalysisRunService(session).create(first_project["id"], "First request")
        second_run, _ = AnalysisRunService(session).create(second_project["id"], "Second request")

    response = client.get(f"/api/projects/{first_project['id']}/analysis")

    assert response.status_code == 200
    assert response.json()["id"] == first_run.id
    assert response.json()["id"] != second_run.id


def test_analysis_start_is_idempotent_while_run_is_active(client) -> None:
    project = client.post("/api/projects", json={"name": "Idempotent"}).json()
    client.app.state.llm_provider = MockLLMProvider(
        [
            {
                "action": "ask_user",
                "question": "Clarify scope",
                "reason": "Scope missing",
            }
        ]
    )
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("sales.csv", "sales\n1\n", "text/csv")},
    )

    first = client.post(f"/api/projects/{project['id']}/analysis", json={"message": "Analyze"})
    second = client.post(
        f"/api/projects/{project['id']}/analysis", json={"message": "Analyze again"}
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]


def test_sse_reconnect_only_returns_events_after_cursor(client) -> None:
    project = client.post("/api/projects", json={"name": "SSE"}).json()
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze")
        service = AnalysisRunService(session)
        service.event(run.id, "analysis.status", {"step": 1})
        service.event(run.id, "analysis.completed", {"status": "completed"})
        run.status = "completed"
        run_id = run.id

    response = client.get(f"/api/analysis/{run_id}/events", headers={"Last-Event-ID": "1"})

    assert response.status_code == 200
    assert "id: 1\n" not in response.text
    assert "id: 2\n" in response.text
    assert "id: 3\n" in response.text
    assert "event: analysis.completed" in response.text


def test_stop_endpoint_is_idempotent(client) -> None:
    project = client.post("/api/projects", json={"name": "Stop API"}).json()
    client.app.state.llm_provider = None
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze")
        run_id = run.id

    first = client.post(f"/api/analysis/{run_id}/stop")
    second = client.post(f"/api/analysis/{run_id}/stop")

    assert first.json()["status"] == "stopped"
    assert second.json()["status"] == "stopped"
    with client.app.state.database.session() as session:
        assert session.get(AnalysisRun, run_id).status == "stopped"


def test_start_without_llm_does_not_leave_pending_run(client) -> None:
    project = client.post("/api/projects", json={"name": "No LLM"}).json()
    response = client.post(f"/api/projects/{project['id']}/analysis", json={"message": "Analyze"})

    assert response.status_code == 503
    with client.app.state.database.session() as session:
        runs = list(session.query(AnalysisRun).filter(AnalysisRun.project_id == project["id"]))
    assert runs == []


def test_failed_analysis_can_retry_without_creating_another_run(client) -> None:
    project = client.post("/api/projects", json={"name": "Retry API"}).json()
    client.app.state.llm_provider = MockLLMProvider(
        [{"action": "ask_user", "question": "Continue?", "reason": "Need confirmation"}]
    )
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze")
        run.status = "failed"
        run.state = "EVALUATE"
        run.error_message = "Finding references a missing artifact: data/anomalies.json"
        run.step_count = 30
        run.execution_count = 20
        run.code_retry_count = 3
        run_id = run.id

    response = client.post(f"/api/analysis/{run_id}/retry")

    assert response.status_code == 202
    assert response.json()["id"] == run_id
    assert response.json()["error_message"] is None
    assert response.json()["step_count"] == 0
    assert response.json()["execution_count"] == 0
    assert response.json()["code_retry_count"] == 0
    with client.app.state.database.session() as session:
        runs = list(session.query(AnalysisRun).filter(AnalysisRun.project_id == project["id"]))
        retry_event = next(
            event for event in runs[0].events if event.event_type == "analysis.retry_started"
        )
    assert len(runs) == 1
    assert json.loads(retry_event.data_json)["previous_error"] == (
        "Finding references a missing artifact: data/anomalies.json"
    )


def test_retry_rejects_non_failed_analysis(client) -> None:
    project = client.post("/api/projects", json={"name": "Invalid retry"}).json()
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze")
        run_id = run.id

    response = client.post(f"/api/analysis/{run_id}/retry")

    assert response.status_code == 409


def test_report_retry_returns_immediately_and_runs_in_background(client, monkeypatch) -> None:
    project = client.post("/api/projects", json={"name": "Async report"}).json()
    client.app.state.llm_provider = MockLLMProvider([])
    with client.app.state.database.session() as session:
        run, _ = AnalysisRunService(session).create(project["id"], "Analyze")
        run.status = "failed"
        run.state = "REPORT"
        run.error_message = "Model unavailable"
        run_id = run.id

    started = Event()
    release = Event()

    async def delayed_report(self, delayed_run_id: str) -> None:
        assert delayed_run_id == run_id
        started.set()
        await asyncio.to_thread(release.wait)

    monkeypatch.setattr(AnalysisOrchestrator, "regenerate_report", delayed_report)

    response = client.post(f"/api/analysis/{run_id}/report")

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert response.json()["state"] == "REPORT"
    assert response.json()["error_message"] is None
    assert started.wait(timeout=1)
    release.set()
