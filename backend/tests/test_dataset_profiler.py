import json
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from app.services.dataset_profiler import DatasetProfiler
from app.services.workspace import PathResolver, WorkspaceService


def make_workspace(tmp_path: Path) -> tuple[str, PathResolver]:
    project_id = "pj_" + "b" * 32
    WorkspaceService(tmp_path).create(project_id)
    return project_id, PathResolver(tmp_path)


def test_profiles_csv_statistics_dates_missing_and_limits(tmp_path: Path) -> None:
    project_id, resolver = make_workspace(tmp_path)
    rows = [
        {
            "date": f"2026-01-{index % 28 + 1:02d}",
            "sales": index if index != 3 else None,
            "region": f"region-{index}",
            "note": "x" * 500,
        }
        for index in range(30)
    ]
    pd.DataFrame(rows).to_csv(resolver.resolve(project_id, "input/sales.csv"), index=False)

    profile = DatasetProfiler(resolver).profile_project(project_id)

    assert not profile.errors
    sheet = profile.files[0].sheets[0]
    assert sheet.row_count == 30
    assert len(sheet.sample) == 10
    assert len(sheet.sample[0]["note"]) == 200
    columns = {column.name: column for column in sheet.columns}
    assert columns["sales"].numeric_statistics.maximum == 29
    assert columns["sales"].missing_rate > 0
    assert columns["date"].date_range.minimum.startswith("2026-01-01")
    assert len(columns["region"].top_values) == 10
    saved = json.loads(resolver.resolve(project_id, "context/dataset_profile.json").read_text())
    assert saved["files"][0]["filename"] == "sales.csv"


def test_profiles_xlsx_multiple_sheets_and_empty_sheet(tmp_path: Path) -> None:
    project_id, resolver = make_workspace(tmp_path)
    workbook = resolver.resolve(project_id, "input/book.xlsx")
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        pd.DataFrame({"value": [1, 2]}).to_excel(writer, sheet_name="Data", index=False)
        pd.DataFrame().to_excel(writer, sheet_name="Empty", index=False)

    profile = DatasetProfiler(resolver).profile_project(project_id)

    sheets = {sheet.name: sheet for sheet in profile.files[0].sheets}
    assert sheets["Data"].row_count == 2
    assert sheets["Empty"].row_count == 0
    assert sheets["Empty"].columns == []


def test_profiles_semicolon_csv_with_gb18030_encoding(tmp_path: Path) -> None:
    project_id, resolver = make_workspace(tmp_path)
    path = resolver.resolve(project_id, "input/chinese.csv")
    path.write_bytes("地区;销售额\n华东;100\n".encode("gb18030"))

    profile = DatasetProfiler(resolver).profile_project(project_id)

    sheet = profile.files[0].sheets[0]
    assert [column.name for column in sheet.columns] == ["地区", "销售额"]


def test_corrupt_upload_is_preserved_and_reports_profile_failure(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "Corrupt"}).json()
    response = client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("broken.xlsx", b"not an xlsx", "application/octet-stream")},
    )

    assert response.status_code == 201
    assert response.json()["profile_status"] == "failed"
    tree = client.get(f"/api/projects/{project['id']}/files").json()
    input_node = next(node for node in tree if node["name"] == "input")
    assert input_node["children"][0]["name"] == "broken.xlsx"
    context_node = next(node for node in tree if node["name"] == "context")
    assert context_node["children"][0]["name"] == "dataset_profile.json"


def test_xls_reader_is_declared() -> None:
    import xlrd

    assert xlrd.__version__
