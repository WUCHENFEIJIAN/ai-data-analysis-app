import re
import shutil
from pathlib import Path
from urllib.parse import unquote

from app.core.errors import ValidationError

WORKSPACE_DIRECTORIES = (
    "input",
    "context",
    "plans",
    "scripts",
    "data",
    "generated",
    "charts",
    "analysis",
    "reports",
    "logs",
)
PROJECT_ID_PATTERN = re.compile(r"^pj_[0-9a-f]{32}$")


class PathResolver:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def project_root(self, project_id: str) -> Path:
        if not PROJECT_ID_PATTERN.fullmatch(project_id):
            raise ValidationError("Invalid project identifier")
        return self._ensure_within(self.workspace_root / project_id, self.workspace_root)

    def resolve(self, project_id: str, relative_path: str) -> Path:
        decoded = relative_path
        for _ in range(3):
            next_value = unquote(decoded)
            if next_value == decoded:
                break
            decoded = next_value
        decoded = decoded.replace("\\", "/")
        candidate_path = Path(decoded)
        if not decoded or candidate_path.is_absolute() or re.match(r"^[A-Za-z]:", decoded):
            raise ValidationError("Invalid workspace path")
        if any(part in {"", ".", ".."} for part in candidate_path.parts):
            raise ValidationError("Invalid workspace path")
        return self._ensure_within(
            self.project_root(project_id) / candidate_path, self.project_root(project_id)
        )

    @staticmethod
    def _ensure_within(candidate: Path, parent: Path) -> Path:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(parent.resolve())
        except ValueError as exc:
            raise ValidationError("Path escapes project workspace") from exc
        return resolved


class WorkspaceService:
    def __init__(self, workspace_root: Path) -> None:
        self.resolver = PathResolver(workspace_root)

    def create(self, project_id: str) -> Path:
        project_root = self.resolver.project_root(project_id)
        project_root.mkdir(parents=True, exist_ok=False)
        for directory in WORKSPACE_DIRECTORIES:
            (project_root / directory).mkdir()
        return project_root

    def delete(self, project_id: str) -> None:
        project_root = self.resolver.project_root(project_id)
        if project_root.exists():
            shutil.rmtree(project_root)
