"""Application-owned validation and atomic persistence of Report Evidence."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.schemas.findings import Findings
from app.services.metric_contract import MetricValidationError, MetricValidator
from app.services.report_evidence import (
    EvidenceJsonSelector,
    EvidenceTableSelector,
    ReportEvidenceManifest,
    manifest_metric_reference_issues,
)
from app.services.report_inputs import ReportInputCollector
from app.services.workspace import PathResolver


class ReportEvidenceDeclarationService:
    def __init__(self, session: Session, resolver: PathResolver) -> None:
        self.session = session
        self.resolver = resolver

    def declare(self, project_id: str, manifest: ReportEvidenceManifest) -> Path:
        findings = self._findings(project_id)
        issues = self.validate_references(project_id, manifest, findings)
        if issues:
            raise ValidationError("Report Evidence declaration is invalid: " + "; ".join(issues))
        target = self.resolver.resolve(project_id, "analysis/report_evidence.json")
        payload = manifest.model_dump_json(indent=2).encode("utf-8")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".report_evidence.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return target

    def validate_references(
        self, project_id: str, manifest: ReportEvidenceManifest, findings: Findings
    ) -> list[str]:
        issues: list[str] = []
        metrics = list(manifest.metrics)
        known_metric_ids = {metric.metric_id for metric in metrics}
        for kpi in manifest.kpis:
            if kpi.metric_definition is not None:
                if kpi.metric_definition.metric_id != kpi.metric:
                    issues.append(f"KPI {kpi.id} inline metric_definition does not match metric")
                if kpi.metric_definition.metric_id not in known_metric_ids:
                    metrics.append(kpi.metric_definition)
                    known_metric_ids.add(kpi.metric_definition.metric_id)
        for artifact in manifest.artifacts:
            if artifact.chart:
                for series in artifact.chart.series:
                    if (
                        series.metric_definition
                        and series.metric_definition.metric_id not in known_metric_ids
                    ):
                        metrics.append(series.metric_definition)
                        known_metric_ids.add(series.metric_definition.metric_id)
            if artifact.table:
                for column in artifact.table.columns:
                    if (
                        column.metric_definition
                        and column.metric_definition.metric_id not in known_metric_ids
                    ):
                        metrics.append(column.metric_definition)
                        known_metric_ids.add(column.metric_definition.metric_id)
        try:
            MetricValidator.validate(metrics)
        except MetricValidationError as exc:
            issues.append(f"metric contract: {exc}")
        issues.extend(manifest_metric_reference_issues(manifest))

        finding_by_id = {finding.id: finding for finding in findings.findings}
        claim_to_finding = {
            claim.claim_id: finding.id for finding in findings.findings for claim in finding.claims
        }
        referenced_metrics = {
            *(kpi.metric for kpi in manifest.kpis),
            *(
                series.metric
                for artifact in manifest.artifacts
                if artifact.chart
                for series in artifact.chart.series
            ),
            *(
                column.metric
                for artifact in manifest.artifacts
                if artifact.table
                for column in artifact.table.columns
                if column.metric
            ),
            *(
                metric_id
                for finding in findings.findings
                for claim in finding.claims
                for metric_id in claim.evidence_metric_ids
            ),
        }
        for metric_id in sorted(referenced_metrics - known_metric_ids):
            issues.append(f"unknown metric_id: {metric_id}")

        catalog = ReportInputCollector(self.session, self.resolver, None).catalog(
            project_id, manifest
        )
        catalog_by_path = {entry.path: entry for entry in catalog}
        for finding in findings.findings:
            for claim in finding.claims:
                for path in claim.evidence_artifact_paths:
                    if path not in catalog_by_path:
                        issues.append(
                            f"Claim {claim.claim_id} references missing Artifact: {path}"
                        )
        for metric in metrics:
            if metric.source_artifact not in catalog_by_path:
                issues.append(f"metric source Artifact does not exist: {metric.source_artifact}")
        for kpi in manifest.kpis:
            issues.extend(
                self._binding_issues(
                    kpi.finding_ids,
                    kpi.supports_claim_ids,
                    finding_by_id,
                    claim_to_finding,
                    f"KPI {kpi.id}",
                )
            )
            entry = catalog_by_path.get(kpi.artifact_path)
            if entry is None:
                issues.append(f"KPI Artifact does not exist: {kpi.artifact_path}")
            else:
                issues.extend(self._selector_issues(project_id, kpi.artifact_path, kpi.selector))
        for artifact in manifest.artifacts:
            issues.extend(
                self._binding_issues(
                    artifact.finding_ids,
                    artifact.supports_claim_ids,
                    finding_by_id,
                    claim_to_finding,
                    artifact.artifact_path,
                )
            )
            entry = catalog_by_path.get(artifact.artifact_path)
            if entry is None:
                issues.append(f"Artifact does not exist: {artifact.artifact_path}")
                continue
            structure = entry.structure or {}
            fields = {
                item.get("name")
                for item in structure.get("columns", structure.get("fields", []))
                if isinstance(item, dict)
            }
            if artifact.chart and entry.kind != "image":
                issues.extend(
                    self._binding_issues(
                        artifact.finding_ids,
                        artifact.chart.supports_claim_ids,
                        finding_by_id,
                        claim_to_finding,
                        f"Chart {artifact.artifact_path}",
                    )
                )
                required = {
                    artifact.chart.x_field,
                    *(series.field for series in artifact.chart.series),
                }
                if artifact.chart.sort_by:
                    required.add(artifact.chart.sort_by)
                for field in sorted(required - fields):
                    issues.append(
                        f"Chart field does not exist in {artifact.artifact_path}: {field}"
                    )
            if artifact.table:
                issues.extend(
                    self._binding_issues(
                        artifact.finding_ids,
                        artifact.table.supports_claim_ids,
                        finding_by_id,
                        claim_to_finding,
                        f"Table {artifact.artifact_path}",
                    )
                )
                for field in sorted({column.field for column in artifact.table.columns} - fields):
                    issues.append(
                        f"Table field does not exist in {artifact.artifact_path}: {field}"
                    )
        return list(dict.fromkeys(issues))

    def _selector_issues(self, project_id: str, artifact_path: str, selector: Any) -> list[str]:
        try:
            value = self._load(project_id, artifact_path)
            if isinstance(selector, EvidenceJsonSelector):
                for part in selector.path:
                    value = value[part]
            elif isinstance(selector, EvidenceTableSelector):
                for part in selector.records_path:
                    value = value[part]
                if isinstance(value, dict):
                    value = value.get("records", value.get("data", value))
                value = value[selector.row][selector.field]
            if value is None or isinstance(value, (dict, list)):
                raise TypeError("selector must resolve to one scalar value")
            if isinstance(value, bool):
                raise TypeError("KPI selector must resolve to a numeric value")
            float(str(value).replace(",", "").rstrip("%"))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            csv.Error,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as exc:
            return [f"invalid selector for {artifact_path}: {type(exc).__name__}: {exc}"]
        return []

    def _load(self, project_id: str, artifact_path: str) -> Any:
        path = self.resolver.resolve(project_id, artifact_path)
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))
        return json.loads(path.read_text(encoding="utf-8"))

    def _findings(self, project_id: str) -> Findings:
        path = self.resolver.resolve(project_id, "analysis/findings.json")
        try:
            return Findings.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ValidationError(
                "Valid findings are required before declaring Report Evidence"
            ) from exc

    @staticmethod
    def _binding_issues(
        finding_ids: list[str],
        claim_ids: list[str],
        finding_by_id: dict[str, Any],
        claim_to_finding: dict[str, str],
        label: str,
    ) -> list[str]:
        issues: list[str] = []
        for finding_id in finding_ids:
            if finding_id not in finding_by_id:
                issues.append(f"{label} references unknown finding_id: {finding_id}")
        for claim_id in claim_ids:
            owner = claim_to_finding.get(claim_id)
            if owner is None:
                issues.append(f"{label} references unknown claim_id: {claim_id}")
            elif owner not in finding_ids:
                issues.append(f"{label} claim {claim_id} is not owned by its finding_ids")
        return issues
