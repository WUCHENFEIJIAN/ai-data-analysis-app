from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ValidationError
from app.services.workspace import PathResolver


@pytest.fixture
def project(client: TestClient) -> dict[str, str]:
    return client.post("/api/projects", json={"name": "Files"}).json()


def test_upload_tree_preview_and_unique_name(client: TestClient, project: dict[str, str]) -> None:
    content = "region,sales\nEast,100\nWest,80\n"
    first = client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("sales.csv", content, "text/csv")},
    )
    second = client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("sales.csv", content, "text/csv")},
    )
    assert first.status_code == 201
    assert first.json()["path"] == "input/sales.csv"
    assert second.json()["path"] == "input/sales_2.csv"

    tree = client.get(f"/api/projects/{project['id']}/files").json()
    input_node = next(node for node in tree if node["name"] == "input")
    assert [child["name"] for child in input_node["children"]] == ["sales.csv", "sales_2.csv"]

    preview = client.get(f"/api/projects/{project['id']}/files/input/sales.csv").json()
    assert preview["kind"] == "csv"
    assert preview["columns"] == ["region", "sales"]
    assert preview["rows"] == [["East", "100"], ["West", "80"]]


def test_upload_rejects_extension_and_size(
    client: TestClient, project: dict[str, str], settings: Settings
) -> None:
    invalid = client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("run.py", b"print(1)", "text/plain")},
    )
    assert invalid.status_code == 422

    settings.max_upload_bytes = 4
    oversized = client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("large.csv", b"12345", "text/csv")},
    )
    assert oversized.status_code == 422
    assert not (settings.workspace_root / project["id"] / "input" / "large.csv").exists()


@pytest.mark.parametrize(
    "unsafe_path",
    ["../secret", "%2e%2e/secret", "%252e%252e/secret", "/absolute", "C:/Windows/system.ini"],
)
def test_path_resolver_rejects_traversal(tmp_path: Path, unsafe_path: str) -> None:
    resolver = PathResolver(tmp_path)
    project_id = "pj_" + "a" * 32
    (tmp_path / project_id).mkdir()
    with pytest.raises(ValidationError):
        resolver.resolve(project_id, unsafe_path)


def test_large_csv_preview_is_limited(client: TestClient, project: dict[str, str]) -> None:
    content = "value\n" + "\n".join(str(index) for index in range(500))
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("large.csv", content, "text/csv")},
    )
    preview = client.get(f"/api/projects/{project['id']}/files/input/large.csv").json()
    assert len(preview["rows"]) == 100
    assert preview["truncated"] is True
