from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import PROJECT_ROOT, Settings


def test_settings_resolve_workspace_root(tmp_path: Path) -> None:
    settings = Settings(workspace_root=tmp_path / "workspace")
    assert settings.workspace_root.is_absolute()


def test_relative_project_paths_are_resolved_from_repository_root() -> None:
    settings = Settings(workspace_root="./workspaces", skill_root="./DAskill/data-analysis")

    assert settings.workspace_root == PROJECT_ROOT / "workspaces"
    assert settings.skill_root == PROJECT_ROOT / "DAskill" / "data-analysis"


def test_settings_reject_unsafe_execution_limits() -> None:
    with pytest.raises(ValidationError):
        Settings(max_agent_steps=0)


def test_local_frontend_origin_allows_localhost_and_loopback(tmp_path: Path) -> None:
    settings = Settings(workspace_root=tmp_path, frontend_origin="http://localhost:3000")

    assert settings.frontend_origins == ["http://127.0.0.1:3000", "http://localhost:3000"]
