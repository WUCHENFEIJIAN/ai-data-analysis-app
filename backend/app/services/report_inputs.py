"""Collect a small, complete Report Editor context from existing analysis outputs."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.models import Artifact
from app.schemas.findings import Findings
from app.services.artifact_schema import ArtifactSchemaInspector
from app.services.metric_contract import MetricDefinition, MetricValidator
from app.services.report_evidence import ReportEvidenceManifest
from app.services.report_ready_artifacts import merge_report_schema
from app.services.report_reportability import apply_reportability
from app.services.workspace import PathResolver


@dataclass(frozen=True)
class ArtifactEntry:
    id: str
    path: str
    kind: str
    sha256: str
    media_type: str
    size_bytes: int
    declared_usage: str = "none"
    report_ready: bool = False
    structure: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "artifact_path": self.path,
            "kind": self.kind,
            "sha256": self.sha256,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "declared_usage": self.declared_usage,
            "report_ready": self.report_ready,
            "structure": self.structure or {},
        }


@dataclass(frozen=True)
class ReportInputs:
    analysis_topic: str
    title: str
    subtitle: str | None
    requested_style: str | None
    user_request: str
    dataset_profile: Any
    analysis_plan: Any
    findings: Findings
    metrics: list[MetricDefinition]
    catalog: list[ArtifactEntry]
    evidence_manifest: ReportEvidenceManifest = field(
        default_factory=lambda: ReportEvidenceManifest(schema_version="1.0")
    )
    report_skill: str = ""

    def prompt_payload(self) -> dict[str, Any]:
        from app.services.report_editorial_context import EditorialContextBuilder

        return EditorialContextBuilder.build(self)


class ReportInputCollector:
    def __init__(self, session: Session, resolver: PathResolver, skill_loader: Any = None) -> None:
        self.session = session
        self.resolver = resolver
        self.skill_loader = skill_loader

    def collect(
        self,
        project_id: str,
        user_request: str,
        title: str | None,
        subtitle: str | None = None,
        style: str | None = None,
    ) -> ReportInputs:
        findings = self._required_findings(project_id)
        plan = self._optional_json(project_id, "plans/analysis_plan.json")
        profile = self._optional_json(project_id, "context/dataset_profile.json")
        manifest = self._optional_manifest(project_id)
        catalog = self.catalog(project_id, manifest)
        metrics = self._collect_metrics(project_id, manifest, catalog)
        analysis_topic = _resolve_topic(plan, title)
        return ReportInputs(
            analysis_topic=analysis_topic,
            title=analysis_topic,
            subtitle=subtitle,
            requested_style=style,
            user_request=user_request,
            dataset_profile=_dataset_summary(profile),
            analysis_plan=_plan_summary(plan),
            findings=findings,
            metrics=metrics,
            catalog=catalog,
            evidence_manifest=manifest or ReportEvidenceManifest(schema_version="1.0"),
            report_skill="",
        )

    def catalog(
        self, project_id: str, manifest: ReportEvidenceManifest | None = None
    ) -> list[ArtifactEntry]:
        root = self.resolver.project_root(project_id)
        usage_by_path: dict[str, str] = {}
        if manifest is not None:
            usage_by_path = {item.artifact_path: item.usage for item in manifest.artifacts}
            for kpi in manifest.kpis:
                usage_by_path.setdefault(kpi.artifact_path, "evidence_only")
        known = {
            item.path: item
            for item in self.session.scalars(
                select(Artifact).where(Artifact.project_id == project_id)
            )
        }
        entries: list[ArtifactEntry] = []
        if not root.is_dir():
            return entries
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.relative_to(root).parts[0] in {"input", "logs"}:
                continue
            relative = path.relative_to(root).as_posix()
            kind = _kind_for(relative)
            if kind is None:
                continue
            artifact = known.get(relative)
            artifact_id = (
                artifact.id
                if artifact
                else f"artifact_{hashlib.sha1(relative.encode()).hexdigest()[:16]}"
            )
            entries.append(
                ArtifactEntry(
                    id=artifact_id,
                    path=relative,
                    kind=kind,
                    sha256=_sha256(path),
                    media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    size_bytes=path.stat().st_size,
                    declared_usage=usage_by_path.get(relative, "none"),
                    report_ready=bool(artifact and artifact.report_schema_json),
                    structure=merge_report_schema(
                        _artifact_structure(path, kind),
                        _report_schema(artifact.report_schema_json if artifact else None),
                    ),
                )
            )
        return entries

    def _catalog(self, project_id: str) -> list[ArtifactEntry]:
        return self.catalog(project_id)

    def evidence_manifest(self, project_id: str) -> ReportEvidenceManifest | None:
        return self._optional_manifest(project_id)

    def _required_findings(self, project_id: str) -> Findings:
        data = self._json_file(project_id, "analysis/findings.json", required=True)
        try:
            findings = apply_reportability(Findings.model_validate(data))
        except Exception as exc:
            raise ValidationError("Required report input is invalid: findings.json") from exc
        if not findings.findings:
            raise ValidationError("Analysis is not complete: findings.json has no findings")
        return findings

    def _optional_manifest(self, project_id: str) -> ReportEvidenceManifest | None:
        path = self.resolver.resolve(project_id, "analysis/report_evidence.json")
        if not path.is_file():
            return None
        try:
            return ReportEvidenceManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return None

    def _collect_metrics(
        self,
        project_id: str,
        manifest: ReportEvidenceManifest | None,
        catalog: list[ArtifactEntry],
    ) -> list[MetricDefinition]:
        collected: dict[str, MetricDefinition] = {}

        def add(definition: MetricDefinition | None) -> None:
            if definition is None or definition.metric_id in collected:
                return
            collected[definition.metric_id] = definition

        metrics_path = self.resolver.resolve(project_id, "analysis/metrics.json")
        metrics_file = self._optional_json(project_id, "analysis/metrics.json")
        if metrics_path.is_file():
            canonical_metrics = _metric_candidates(metrics_file)
            empty_registry = metrics_file == [] or (
                isinstance(metrics_file, dict) and metrics_file.get("metrics") == []
            )
            if not canonical_metrics and not empty_registry:
                raise ValidationError("Canonical Metric Registry is invalid: metrics.json")
            try:
                MetricValidator.validate(canonical_metrics)
            except ValueError as exc:
                raise ValidationError("Canonical Metric Registry is invalid: metrics.json") from exc
            for item in canonical_metrics:
                add(item)
        # Canonical Analysis output wins. The manifest remains readable for
        # existing projects, but is no longer the only semantic source.
        if manifest is not None:
            for item in manifest.metrics:
                add(item)
            for kpi in manifest.kpis:
                add(kpi.metric_definition)
        for entry in catalog:
            if entry.kind != "json" or not entry.path.startswith("data/"):
                continue
            payload = self._optional_json(project_id, entry.path)
            for item in _metric_candidates(payload):
                add(item)
        return list(collected.values())

    def _optional_json(self, project_id: str, relative_path: str) -> Any:
        return self._json_file(project_id, relative_path, required=False)

    def _json_file(self, project_id: str, relative_path: str, required: bool) -> Any:
        path = self.resolver.resolve(project_id, relative_path)
        if not path.is_file():
            if required:
                raise ValidationError(
                    f"Required report input is missing: {Path(relative_path).name}"
                )
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            if required:
                raise ValidationError(
                    f"Required report input is invalid: {Path(relative_path).name}"
                ) from exc
            return None


def _kind_for(relative: str) -> str | None:
    suffix = Path(relative).suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".json":
        return "json"
    if suffix in {".png", ".svg", ".jpg", ".jpeg", ".webp"}:
        return "image"
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_structure(path: Path, kind: str) -> dict[str, Any]:
    return ArtifactSchemaInspector().inspect(path) or {"record_kind": kind}


def _report_schema(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _metric_candidates(payload: Any) -> list[MetricDefinition]:
    if payload is None:
        return []
    values: list[Any]
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        if "metric_id" in payload:
            values = [payload]
        elif isinstance(payload.get("metrics"), list):
            values = payload["metrics"]
        else:
            values = []
    else:
        values = []
    metrics: list[MetricDefinition] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        try:
            metrics.append(MetricDefinition.model_validate(item))
        except Exception:
            continue
    return metrics


def _dataset_summary(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    files = []
    field_names: list[str] = []
    row_count = 0
    time_range = profile.get("time_range") or profile.get("date_range")
    for item in profile.get("files", [])[:20]:
        if not isinstance(item, dict):
            continue
        file_info = {
            "name": item.get("name") or item.get("path"),
            "sheets": [],
        }
        for sheet in item.get("sheets", [])[:10]:
            if not isinstance(sheet, dict):
                continue
            columns = [
                column.get("name", column) if isinstance(column, dict) else column
                for column in sheet.get("columns", [])[:40]
            ]
            field_names.extend(str(column) for column in columns if column)
            rows = sheet.get("row_count") or sheet.get("rows") or 0
            if isinstance(rows, int):
                row_count += rows
            file_info["sheets"].append(
                {
                    "name": sheet.get("name"),
                    "row_count": rows,
                    "columns": columns,
                }
            )
        files.append(file_info)
    return {
        "file_count": len(files),
        "approx_row_count": row_count or profile.get("row_count"),
        "time_range": time_range,
        "fields": list(dict.fromkeys(field_names))[:60],
        "files": files,
    }


def _plan_summary(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {}
    tasks = []
    for item in plan.get("tasks", [])[:20]:
        if isinstance(item, dict):
            tasks.append(
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "goal": item.get("goal"),
                }
            )
        elif isinstance(item, str):
            tasks.append({"title": item})
    return {
        "analysis_topic": plan.get("analysis_topic") or plan.get("title"),
        "objective": plan.get("objective"),
        "tasks": tasks,
    }


def _resolve_topic(plan: Any, requested_title: str | None) -> str:
    generic = {"analysis report", "report", "分析报告"}
    if isinstance(plan, dict):
        for key in ("analysis_topic", "title", "objective"):
            value = plan.get(key)
            if isinstance(value, str) and value.strip() and value.strip().lower() not in generic:
                return value.strip()
    if isinstance(requested_title, str) and requested_title.strip():
        return requested_title.strip()
    return "Analysis report"
