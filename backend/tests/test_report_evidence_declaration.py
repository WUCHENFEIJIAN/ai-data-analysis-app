import json

import pytest

from app.core.errors import ValidationError
from app.schemas.findings import Findings
from app.services.artifacts import ArtifactService
from app.services.report_evidence import ReportEvidenceManifest
from app.services.report_evidence_declaration import ReportEvidenceDeclarationService
from app.services.workspace import PathResolver


def manifest(x_field: str = "segment") -> ReportEvidenceManifest:
    return ReportEvidenceManifest.model_validate(
        {
            "schema_version": "1.0",
            "metrics": [
                {
                    "metric_id": "metric_x",
                    "label": "Metric X",
                    "value": 10,
                    "aggregation": "sum",
                    "semantic_type": "measure",
                    "unit_family": "currency",
                    "definition": "Sum of metric_x",
                    "source_artifact": "data/result.csv",
                }
            ],
            "kpis": [
                {
                    "id": "metric_x",
                    "label": "Metric X",
                    "metric": "metric_x",
                    "artifact_path": "data/result.csv",
                    "selector": {
                        "type": "table",
                        "records_path": [],
                        "row": 0,
                        "field": "metric_x",
                    },
                    "format": "number",
                    "purpose": "Show the verified total",
                    "presentation_roles": ["overview"],
                }
            ],
            "artifacts": [
                {
                    "artifact_path": "data/result.csv",
                    "usage": "visual_source",
                    "finding_ids": ["finding_1"],
                    "purpose": "Compare verified segments",
                    "chart": {
                        "chart_type": "bar",
                        "title": "Segment comparison",
                        "x_field": x_field,
                        "series": [
                            {"field": "metric_x", "label": "Metric X", "metric": "metric_x"}
                        ],
                        "source_caption": "Source: result.csv",
                    },
                }
            ],
        }
    )


def prepare(client, settings):
    project = client.post("/api/projects", json={"name": "Evidence declaration"}).json()
    resolver = PathResolver(settings.workspace_root)
    data = resolver.resolve(project["id"], "data/result.csv")
    data.write_text("segment,metric_x\nA,10\n", encoding="utf-8")
    findings = Findings.model_validate(
        {
            "summary": "Summary",
            "findings": [
                {
                    "id": "finding_1",
                    "title": "Finding",
                    "evidence": ["Metric X is 10"],
                    "risk": "Risk",
                    "recommendation": "Recommendation",
                    "related_artifacts": ["data/result.csv"],
                }
            ],
        }
    )
    resolver.resolve(project["id"], "analysis/findings.json").write_text(
        findings.model_dump_json(), encoding="utf-8"
    )
    return project["id"], resolver, data


def test_structured_declaration_is_validated_and_atomically_written(client, settings) -> None:
    project_id, resolver, data = prepare(client, settings)
    with client.app.state.database.session() as session:
        ArtifactService(session).register(project_id, "data/result.csv", data.stat().st_size)
        target = ReportEvidenceDeclarationService(session, resolver).declare(project_id, manifest())

    assert ReportEvidenceManifest.model_validate_json(target.read_text(encoding="utf-8"))
    assert not list(target.parent.glob(".report_evidence.*.tmp"))


def test_invalid_declaration_preserves_previous_manifest(client, settings) -> None:
    project_id, resolver, data = prepare(client, settings)
    target = resolver.resolve(project_id, "analysis/report_evidence.json")
    target.write_text(json.dumps({"previous": "valid"}), encoding="utf-8")
    before = target.read_bytes()
    with client.app.state.database.session() as session:
        ArtifactService(session).register(project_id, "data/result.csv", data.stat().st_size)
        with pytest.raises(ValidationError, match="Chart field does not exist"):
            ReportEvidenceDeclarationService(session, resolver).declare(
                project_id, manifest("missing_field")
            )

    assert target.read_bytes() == before
