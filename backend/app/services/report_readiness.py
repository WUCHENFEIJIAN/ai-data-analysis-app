"""Deprecated. Report generation no longer uses Readiness as a gate.

Kept only so old isolated tests can inspect the historical contract. The normal
Report pipeline must not call this service.
"""

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.schemas.findings import Findings
from app.services.metric_contract import MetricValidationError, MetricValidator
from app.services.report_evidence import (
    EvidenceJsonSelector,
    ReportEvidenceManifest,
    manifest_metric_reference_issues,
)
from app.services.report_inputs import ArtifactEntry, ReportInputCollector
from app.services.workspace import PathResolver


@dataclass(frozen=True)
class ReportReadinessIssue:
    code: str
    stage: str
    stage_rank: int
    message: str
    target: str
    repair: str

    @property
    def identity(self) -> str:
        return f"{self.code}:{self.target}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.identity,
            "code": self.code,
            "stage": self.stage,
            "stage_rank": self.stage_rank,
            "message": self.message,
            "target": self.target,
            "repair": self.repair,
        }


@dataclass(frozen=True)
class ReportReadiness:
    status: str
    analysis_topic: str | None
    missing: tuple[str, ...]
    catalog: tuple[ArtifactEntry, ...] = ()
    issues: tuple[ReportReadinessIssue, ...] = ()
    artifact_fingerprint: str | None = None
    manifest_fingerprint: str | None = None

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    def as_dict(self) -> dict[str, Any]:
        issues = self.issues or tuple(
            ReportReadinessIssue(
                code="legacy.readiness",
                stage="unknown",
                stage_rank=0,
                message=message,
                target=message,
                repair="Repair the reported requirement without weakening validation.",
            )
            for message in self.missing
        )
        missing_analysis_codes = {
            "artifact.finding_reference_missing",
            "artifact.finding_support_missing",
            "artifact.report_data_missing",
            "artifact.catalog_invalid",
        }
        repair_route = (
            "missing_analysis_artifact"
            if any(issue.code in missing_analysis_codes for issue in issues)
            else "evidence_contract"
        )
        has_computed_data = any(
            entry.kind in {"csv", "json"} and entry.path.startswith("data/")
            for entry in self.catalog
        )
        if any(issue.code == "manifest.invalid" for issue in issues) and not has_computed_data:
            repair_route = "missing_analysis_artifact"
        if any(issue.code.startswith("findings.") for issue in issues):
            repair_route = "analysis_fact"
        return {
            "status": self.status,
            "analysis_topic": self.analysis_topic,
            "missing": list(self.missing),
            "artifact_count": len(self.catalog),
            "issues": [issue.as_dict() for issue in issues],
            "issue_ids": [issue.identity for issue in issues],
            "validation_stage": max((issue.stage_rank for issue in issues), default=100),
            "artifact_fingerprint": self.artifact_fingerprint,
            "manifest_fingerprint": self.manifest_fingerprint,
            "repair_route": repair_route,
        }


class ReportReadinessService:
    """Check report inputs without deciding any business-specific chart."""

    GENERIC_TITLES = {"analysis report", "report", "分析报告"}
    RESERVED_DATA_FILES = {
        "analysis/findings.json",
        "plans/analysis_plan.json",
        "context/dataset_profile.json",
        "reports/report_spec.json",
        "analysis/report_evidence.json",
    }

    def __init__(self, session: Session, resolver: PathResolver, skill_loader: Any) -> None:
        self.collector = ReportInputCollector(session, resolver, skill_loader)
        self.resolver = resolver

    def check_project(self, project_id: str, requested_title: str | None = None) -> ReportReadiness:
        plan = self._json(project_id, "plans/analysis_plan.json")
        findings_data = self._json(project_id, "analysis/findings.json")
        manifest_data = self._json(project_id, "analysis/report_evidence.json")
        manifest: ReportEvidenceManifest | None = None
        manifest_error: Exception | None = None
        if manifest_data is not None:
            try:
                manifest = ReportEvidenceManifest.model_validate(manifest_data)
            except Exception as exc:
                manifest_error = exc
        catalog = tuple(self.collector.catalog(project_id, manifest))
        topic = self.resolve_topic(plan, requested_title)
        issues: list[ReportReadinessIssue] = []

        def add(
            code: str,
            stage: str,
            stage_rank: int,
            message: str,
            target: str,
            repair: str,
        ) -> None:
            issues.append(ReportReadinessIssue(code, stage, stage_rank, message, target, repair))

        findings: Findings | None = None
        try:
            findings = Findings.model_validate(findings_data)
        except Exception as exc:
            add(
                "findings.invalid",
                "schema",
                10,
                "valid findings",
                "analysis/findings.json",
                "Return complete_analysis with valid atomic claims and real Artifact references.",
            )
            for target, message in self._schema_error_details("findings", exc):
                add(
                    "findings.schema",
                    "schema",
                    10,
                    message,
                    f"analysis/findings.json#{target}",
                    "Return complete_analysis that fixes this exact Findings field.",
                )

        if manifest is None:
            add(
                "manifest.invalid",
                "schema",
                10,
                "valid report evidence manifest",
                "analysis/report_evidence.json",
                "Atomically create or repair the manifest while preserving complete "
                "evidence coverage.",
            )
            if manifest_error is not None:
                for target, message in self._schema_error_details(
                    "report evidence manifest", manifest_error
                ):
                    add(
                        "manifest.schema",
                        "schema",
                        10,
                        message,
                        f"analysis/report_evidence.json#{target}",
                        "Fix this exact field without removing valid KPI, chart, or table "
                        "evidence.",
                    )

        if not topic:
            add(
                "topic.missing",
                "schema",
                10,
                "analysis_topic",
                "plans/analysis_plan.json#analysis_topic",
                "Preserve a concrete analysis topic from the request or analysis plan.",
            )
        if issues:
            return self._result(project_id, topic, catalog, issues)

        assert manifest is not None and findings is not None
        metric_registry = list(manifest.metrics)
        known_metric_ids = {metric.metric_id for metric in metric_registry}
        for kpi in manifest.kpis:
            if kpi.metric_definition and kpi.metric_definition.metric_id not in known_metric_ids:
                metric_registry.append(kpi.metric_definition)
                known_metric_ids.add(kpi.metric_definition.metric_id)
        try:
            MetricValidator.validate(metric_registry)
        except MetricValidationError as exc:
            add(
                "metric.semantics",
                "metric_contract",
                20,
                "valid metric semantics",
                "analysis/report_evidence.json#metrics",
                f"Repair the Metric Contract exactly as reported: {exc}",
            )
            add(
                "metric.semantics.detail",
                "metric_contract",
                20,
                f"metric semantics: {exc}",
                f"analysis/report_evidence.json#metrics:{exc}",
                "Correct the named definition without renaming referenced metric IDs.",
            )
        for message in manifest_metric_reference_issues(manifest):
            add(
                "metric.reference",
                "metric_contract",
                20,
                message,
                f"analysis/report_evidence.json#{message}",
                "Align this KPI, chart series, or table column unit/scale with the "
                "Metric Definition without renaming the metric ID or removing valid evidence.",
            )
        if issues:
            return self._result(project_id, topic, catalog, issues)

        catalog_by_path = {entry.path: entry for entry in catalog}
        missing_refs = {
            path
            for finding in findings.findings
            for path in finding.related_artifacts
            if path not in catalog_by_path
        }
        for path in sorted(missing_refs):
            add(
                "artifact.finding_reference_missing",
                "artifact_availability",
                30,
                "structured artifacts referenced by findings",
                path,
                "Create the verified Artifact or replace the Finding reference via "
                "complete_analysis.",
            )
        if not missing_refs and any(not finding.related_artifacts for finding in findings.findings):
            add(
                "artifact.finding_support_missing",
                "artifact_availability",
                30,
                "structured artifacts supporting findings",
                "analysis/findings.json#findings.related_artifacts",
                "Attach every material Finding to existing structured evidence.",
            )

        declared_paths = self._declared_paths(manifest)
        data_entries = [
            entry
            for entry in catalog
            if self._is_report_data(entry) and entry.path in declared_paths
        ]
        if not data_entries:
            add(
                "artifact.report_data_missing",
                "artifact_availability",
                30,
                "report-ready structured data artifact",
                "data/",
                "Create report-ready CSV/JSON and declare it in the manifest.",
            )
        if not self._catalog_is_consumable(project_id, catalog):
            add(
                "artifact.catalog_invalid",
                "artifact_availability",
                30,
                "consumable Artifact Catalog",
                "workspace Artifact Catalog",
                "Repair duplicate, missing, or unreadable Artifact entries.",
            )
        has_declared_visual = any(
            artifact.usage == "visual_source"
            and artifact.artifact_path in catalog_by_path
            and (
                catalog_by_path[artifact.artifact_path].kind == "image"
                or self._has_visual_records(project_id, catalog_by_path[artifact.artifact_path])
            )
            for artifact in manifest.artifacts
        )
        if not has_declared_visual:
            add(
                "artifact.visual_missing",
                "artifact_availability",
                30,
                "visualization material selected from analysis evidence",
                "analysis/report_evidence.json#artifacts",
                "Declare chart-ready evidence without reducing Finding coverage.",
            )
        if issues:
            return self._result(project_id, topic, catalog, issues)

        if not manifest.kpis:
            add(
                "binding.kpi_missing",
                "evidence_binding",
                40,
                "explicit KPI definitions",
                "analysis/report_evidence.json#kpis",
                "Declare the required overview and evidence KPI roles from verified metrics.",
            )
        for kpi in manifest.kpis:
            if not self._kpi_is_resolvable(project_id, kpi, catalog_by_path):
                add(
                    "binding.kpi_unresolvable",
                    "evidence_binding",
                    40,
                    "resolvable KPI data",
                    f"{kpi.artifact_path}#kpi:{kpi.id}",
                    "Repair this KPI selector or its data while preserving the KPI definition.",
                )
        finding_artifacts = {
            finding.id: {
                *finding.related_artifacts,
                *(
                    path
                    for claim in finding.claims
                    for path in claim.evidence_artifact_paths
                ),
            }
            for finding in findings.findings
        }
        if any(
            artifact.artifact_path not in finding_artifacts.get(finding_id, set())
            for artifact in manifest.artifacts
            if artifact.usage != "none"
            for finding_id in artifact.finding_ids
        ):
            add(
                "binding.finding_artifact_mismatch",
                "evidence_binding",
                40,
                "evidence artifacts linked by findings",
                "analysis/report_evidence.json#artifacts.finding_ids",
                "Align manifest Finding IDs and Artifact paths with the existing Findings.",
            )
        if issues:
            return self._result(project_id, topic, catalog, issues)

        try:
            self.collector.collect(project_id, user_request="", title=requested_title)
        except ValidationError as exc:
            add(
                "semantic.report_evidence",
                "semantic_validation",
                50,
                "semantically valid report evidence",
                "report evidence contract",
                f"Repair the exact Claim-to-Evidence semantic violation: {exc.message}",
            )
            for detail in (item.strip() for item in exc.message.split(" | ")):
                if not detail:
                    continue
                add(
                    "semantic.report_evidence.detail",
                    "semantic_validation",
                    50,
                    f"report evidence semantics: {detail}",
                    f"report evidence contract:{detail}",
                    "Preserve all material Findings and repair this exact contract mismatch.",
                )
        return self._result(project_id, topic, catalog, issues)

    def _result(
        self,
        project_id: str,
        topic: str | None,
        catalog: tuple[ArtifactEntry, ...],
        issues: list[ReportReadinessIssue],
    ) -> ReportReadiness:
        unique_issues = tuple({issue.identity: issue for issue in issues}.values())
        manifest_path = self.resolver.resolve(project_id, "analysis/report_evidence.json")
        return ReportReadiness(
            status="NOT_READY" if unique_issues else "READY",
            analysis_topic=topic,
            missing=tuple(dict.fromkeys(issue.message for issue in unique_issues)),
            catalog=catalog,
            issues=unique_issues,
            artifact_fingerprint=self._artifact_fingerprint(project_id, catalog),
            manifest_fingerprint=self._file_fingerprint(manifest_path),
        )

    def _artifact_fingerprint(self, project_id: str, catalog: tuple[ArtifactEntry, ...]) -> str:
        paths = {
            "analysis/findings.json",
            "analysis/report_evidence.json",
            *(entry.path for entry in catalog if entry.path.startswith(("data/", "charts/"))),
        }
        digest = hashlib.sha256()
        for relative in sorted(paths):
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            fingerprint = self._file_fingerprint(self.resolver.resolve(project_id, relative))
            digest.update((fingerprint or "missing").encode("ascii"))
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _file_fingerprint(path: Any) -> str | None:
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
        return digest.hexdigest()

    @classmethod
    def resolve_topic(cls, plan: Any, requested_title: str | None = None) -> str | None:
        if isinstance(plan, dict):
            for key in ("analysis_topic", "title", "objective"):
                value = plan.get(key)
                if (
                    isinstance(value, str)
                    and value.strip()
                    and value.strip().lower() not in cls.GENERIC_TITLES
                ):
                    return value.strip()
        if isinstance(requested_title, str) and requested_title.strip():
            title = requested_title.strip()
            if title.lower() not in cls.GENERIC_TITLES:
                return title
        return None

    def _json(self, project_id: str, relative: str) -> Any:
        path = self.resolver.resolve(project_id, relative)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _schema_errors(label: str, exc: Exception, limit: int = 8) -> list[str]:
        return [
            message
            for _, message in ReportReadinessService._schema_error_details(label, exc, limit)
        ]

    @staticmethod
    def _schema_error_details(label: str, exc: Exception, limit: int = 8) -> list[tuple[str, str]]:
        errors_method = getattr(exc, "errors", None)
        if not callable(errors_method):
            return [("<root>", f"{label} schema: {exc}")]
        errors = errors_method()
        details: list[tuple[str, str]] = []
        for error in errors[:limit]:
            location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
            details.append(
                (location, f"{label} schema {location}: {error.get('msg', 'invalid value')}")
            )
        if len(errors) > limit:
            details.append(
                (
                    "<additional>",
                    f"{label} schema: {len(errors) - limit} additional errors omitted",
                )
            )
        return details

    def _is_report_data(self, entry: ArtifactEntry) -> bool:
        if entry.kind not in {"csv", "json"} or entry.path in self.RESERVED_DATA_FILES:
            return False
        return not entry.path.startswith(("context/", "plans/", "analysis/", "reports/"))

    def _load(self, project_id: str, entry: ArtifactEntry) -> Any:
        path = self.resolver.resolve(project_id, entry.path)
        if entry.kind == "csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))
        return json.loads(path.read_text(encoding="utf-8"))

    def _has_metrics(self, project_id: str, entry: ArtifactEntry) -> bool:
        try:
            value = self._load(project_id, entry)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, csv.Error):
            return False
        values = (
            value.get("records", value.get("data", value)) if isinstance(value, dict) else value
        )
        if isinstance(values, list):
            values = [item for row in values if isinstance(row, dict) for item in row.values()]
        elif isinstance(values, dict):
            values = list(values.values())
        else:
            values = [values]
        return any(self._number(item) is not None for item in values)

    @staticmethod
    def _declared_paths(manifest: ReportEvidenceManifest | None) -> set[str]:
        if manifest is None:
            return set()
        return {
            *(kpi.artifact_path for kpi in manifest.kpis),
            *(
                artifact.artifact_path
                for artifact in manifest.artifacts
                if artifact.usage != "none"
            ),
        }

    def _kpi_is_resolvable(
        self, project_id: str, kpi: Any, catalog_by_path: dict[str, ArtifactEntry]
    ) -> bool:
        entry = catalog_by_path.get(kpi.artifact_path)
        if entry is None or entry.kind not in {"csv", "json"}:
            return False
        try:
            value = self._load(project_id, entry)
            if isinstance(kpi.selector, EvidenceJsonSelector):
                for part in kpi.selector.path:
                    value = value[part]
            else:
                for part in kpi.selector.records_path:
                    value = value[part]
                if isinstance(value, dict):
                    value = value.get("records", value.get("data", value))
                value = value[kpi.selector.row][kpi.selector.field]
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            csv.Error,
            KeyError,
            IndexError,
            TypeError,
        ):
            return False
        return value is not None and not isinstance(value, (dict, list))

    def _has_visual_records(self, project_id: str, entry: ArtifactEntry) -> bool:
        try:
            value = self._load(project_id, entry)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, csv.Error):
            return False
        records = (
            value.get("records", value.get("data", value)) if isinstance(value, dict) else value
        )
        return (
            isinstance(records, list)
            and bool(records)
            and isinstance(records[0], dict)
            and len(records[0]) >= 2
        )

    def _catalog_is_consumable(self, project_id: str, catalog: tuple[ArtifactEntry, ...]) -> bool:
        paths = set()
        for entry in catalog:
            if (
                entry.path in paths
                or not entry.path
                or not self.resolver.resolve(project_id, entry.path).is_file()
            ):
                return False
            paths.add(entry.path)
        return True

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(str(value).replace(",", "").rstrip("%"))
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
