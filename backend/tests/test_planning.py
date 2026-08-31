from fastapi.testclient import TestClient

from app.llm.mock import MockLLMProvider


def upload_dataset(client: TestClient, project_id: str) -> None:
    response = client.post(
        f"/api/projects/{project_id}/files",
        files={"file": ("sales.csv", "region,sales\nEast,100\n", "text/csv")},
    )
    assert response.json()["profile_status"] == "completed"


def test_mock_provider_creates_and_persists_analysis_plan(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "Planning"}).json()
    upload_dataset(client, project["id"])
    provider = MockLLMProvider(
        [
            {
                "action": "create_plan",
                "title": "Sales review",
                "objective": "Find drivers",
                "tasks": [
                    {
                        "id": "task_1",
                        "title": "Overview",
                        "goal": "Calculate totals",
                        "sequence": 1,
                    }
                ],
            }
        ]
    )
    client.app.state.llm_provider = provider

    response = client.post(
        f"/api/projects/{project['id']}/analysis/plan",
        json={"message": "Analyze this dataset"},
    )

    assert response.status_code == 200
    assert response.json()["action"] == "create_plan"
    preview = client.get(f"/api/projects/{project['id']}/files/plans/analysis_plan.json").json()
    assert preview["content"]["tasks"][0]["id"] == "task_1"
    messages = client.get(f"/api/projects/{project['id']}/messages").json()
    assert [message["message_type"] for message in messages] == ["text", "plan"]
    sent = provider.requests[0]
    assert all("region,sales" not in message["content"] for message in sent)


def test_mock_provider_can_request_clarification(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "Clarify"}).json()
    upload_dataset(client, project["id"])
    client.app.state.llm_provider = MockLLMProvider(
        [
            {
                "action": "ask_user",
                "question": "How is ROI defined?",
                "reason": "The dataset does not define ROI",
            }
        ]
    )

    response = client.post(
        f"/api/projects/{project['id']}/analysis/plan", json={"message": "Analyze ROI"}
    )

    assert response.json()["action"] == "ask_user"
    messages = client.get(f"/api/projects/{project['id']}/messages").json()
    assert messages[-1]["message_type"] == "question"


def test_plan_requires_readable_dataset(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "No data"}).json()
    client.app.state.llm_provider = MockLLMProvider([])
    response = client.post(
        f"/api/projects/{project['id']}/analysis/plan", json={"message": "Analyze"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
