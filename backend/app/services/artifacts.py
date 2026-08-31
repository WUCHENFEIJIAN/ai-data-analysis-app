import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Artifact


@dataclass(frozen=True)
class SnapshotEntry:
    size: int
    modified_ns: int


class ArtifactDetector:
    IGNORED_DIRECTORIES = {"input", "logs"}

    def snapshot(self, workspace: Path) -> dict[str, SnapshotEntry]:
        entries: dict[str, SnapshotEntry] = {}
        for path in workspace.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(workspace)
            if relative.parts[0] in self.IGNORED_DIRECTORIES:
                continue
            stat = path.stat()
            entries[relative.as_posix()] = SnapshotEntry(stat.st_size, stat.st_mtime_ns)
        return entries

    @staticmethod
    def changed(before: dict[str, SnapshotEntry], after: dict[str, SnapshotEntry]) -> list[str]:
        return sorted(path for path, entry in after.items() if before.get(path) != entry)


class ArtifactService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def register(self, project_id: str, path: str, size_bytes: int) -> Artifact:
        artifact = self.session.scalar(
            select(Artifact).where(Artifact.project_id == project_id, Artifact.path == path)
        )
        artifact_type = self._type(path)
        if artifact is None:
            import uuid

            artifact = Artifact(
                id=f"art_{uuid.uuid4().hex}",
                project_id=project_id,
                path=path,
                artifact_type=artifact_type,
                size_bytes=size_bytes,
            )
            self.session.add(artifact)
        else:
            artifact.artifact_type = artifact_type
            artifact.size_bytes = size_bytes
        self.session.flush()
        return artifact

    def replace_report_schemas(self, project_id: str, declarations: list[object]) -> None:
        """Replace canonical report-ready field bindings for one project."""

        artifacts = list(
            self.session.scalars(select(Artifact).where(Artifact.project_id == project_id))
        )
        by_path = {item.path: item for item in artifacts}
        for artifact in artifacts:
            artifact.report_schema_json = None
        for declaration in declarations:
            path = declaration.artifact_path
            artifact = by_path.get(path)
            if artifact is None:
                raise ValueError(f"Artifact must be registered before schema binding: {path}")
            artifact.report_schema_json = json.dumps(
                declaration.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
            )
        self.session.flush()

    def upsert_report_schemas(self, project_id: str, declarations: list[object]) -> None:
        """Persist validated creation-time bindings without clearing other Artifacts."""

        for declaration in declarations:
            artifact = self.session.scalar(
                select(Artifact).where(
                    Artifact.project_id == project_id,
                    Artifact.path == declaration.artifact_path,
                )
            )
            if artifact is None:
                raise ValueError(
                    "Artifact must be registered before schema binding: "
                    f"{declaration.artifact_path}"
                )
            artifact.report_schema_json = json.dumps(
                declaration.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        self.session.flush()

    @staticmethod
    def _type(path: str) -> str:
        top_level = path.split("/", 1)[0]
        if path == "reports/report_spec.json":
            return "report_spec"
        return {
            "scripts": "script",
            "data": "data",
            "charts": "chart",
            "analysis": "analysis",
            "reports": "report",
            "context": "context",
            "plans": "plan",
        }.get(top_level, "file")
