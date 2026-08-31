from fastapi.testclient import TestClient

from app.core.config import Settings
from app.services.workspace import WORKSPACE_DIRECTORIES


def test_create_list_get_and_delete_project(client: TestClient, settings: Settings) -> None:
    created = client.post("/api/projects", json={"name": "  Sales   Review  "})
    assert created.status_code == 201
    project = created.json()
    assert project["id"].startswith("pj_")
    assert project["name"] == "Sales Review"

    project_root = settings.workspace_root / project["id"]
    assert {path.name for path in project_root.iterdir()} == set(WORKSPACE_DIRECTORIES)
    assert client.get("/api/projects").json()["total"] == 1
    assert client.get(f"/api/projects/{project['id']}").json()["id"] == project["id"]

    response = client.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 204
    assert not project_root.exists()
    assert client.get("/api/projects").json()["total"] == 0


def test_delete_does_not_touch_other_project(client: TestClient, settings: Settings) -> None:
    first = client.post("/api/projects", json={"name": "First"}).json()
    second = client.post("/api/projects", json={"name": "Second"}).json()
    marker = settings.workspace_root / second["id"] / "data" / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    client.delete(f"/api/projects/{first['id']}")

    assert marker.read_text(encoding="utf-8") == "keep"


def test_empty_project_name_has_consistent_error(client: TestClient) -> None:
    response = client.post("/api/projects", json={"name": "   "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_project_creation_cors_accepts_loopback_frontend(client: TestClient) -> None:
    response = client.options(
        "/api/projects",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
