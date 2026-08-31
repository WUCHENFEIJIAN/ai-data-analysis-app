import json

import pytest

from app.agent.run_context import RunContextBuilder
from app.llm.mock import MockLLMProvider
from app.services.artifacts import ArtifactService
from app.services.report_evidence import ReportEvidenceManifest
from app.services.report_evidence_declaration import ReportEvidenceDeclarationService
from app.services.report_fallback import FallbackSpecBuilder
from app.services.report_inputs import ReportInputCollector
from app.services.report_planner import ReportPlanner
from app.services.report_renderer import ReportRenderer
from app.services.workspace import PathResolver
from app.skills.loader import SkillLoader

DATASETS = [
    {
        "name": "service-operations",
        "dimension": "issue_type",
        "metrics": [
            ("resolution_time", "duration", "duration", "hours", [8, 5]),
            ("resolution_rate", "rate", "percentage", "%", [72, 91]),
        ],
    },
    {
        "name": "inventory-operations",
        "dimension": "item_class",
        "first_presentation": "table",
        "metrics": [
            ("stock_quantity", "quantity", "quantity", "units", [120, 75]),
            ("stockout_count", "count", "count", "events", [4, 9]),
        ],
    },
    {
        "name": "neutral-fields",
        "dimension": "category_a",
        "metrics": [
            ("metric_x", "quantity", "quantity", "u", [12, 19]),
            ("rate_z", "rate", "percentage", "%", [35, 62]),
        ],
    },
]


def _metric_definition(metric, semantic_type, unit_family, unit, values, path):
    definition = {
        "metric_id": metric,
        "label": metric,
        "value": max(values),
        "aggregation": "sum",
        "semantic_type": semantic_type,
        "unit_family": unit_family,
        "unit": unit,
        "definition": "Precomputed synthetic measure",
        "source_artifact": path,
    }
    if unit_family == "count":
        definition.update(count_semantics="event_count", is_distinct=False)
    return definition


def _prepare_generic_project(client, settings, case):
    project = client.post("/api/projects", json={"name": case["name"]}).json()
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("source.csv", "key,value\nA,1\n", "text/csv")},
    )
    resolver = PathResolver(settings.workspace_root)
    resolver.resolve(project["id"], "plans/analysis_plan.json").write_text(
        json.dumps({"analysis_topic": case["name"]}), encoding="utf-8"
    )

    artifacts = []
    metrics = []
    claims = []
    related = []
    for index, (metric, semantic_type, unit_family, unit, values) in enumerate(
        case["metrics"], start=1
    ):
        path = f"data/series_{index}.csv"
        resolver.resolve(project["id"], path).write_text(
            f"{case['dimension']},{metric}\nA,{values[0]}\nB,{values[1]}\n",
            encoding="utf-8",
        )
        claim_id = f"claim_{index}"
        metrics.append(_metric_definition(metric, semantic_type, unit_family, unit, values, path))
        claims.append(
            {
                "claim_id": claim_id,
                "statement": f"{metric} has a verified category difference",
                "priority": "primary" if index == 1 else "secondary",
                "strength": 0.9 if index == 1 else 0.7,
                "evidence_metric_ids": [metric],
                "evidence_artifact_paths": [path],
            }
        )
        related.append(path)
        artifact = {
            "artifact_path": path,
            "usage": "visual_source",
            "finding_ids": ["finding_1"],
            "purpose": f"Show the measured {metric} comparison",
            "evidence_role": "primary" if index == 1 else "supporting",
            "supports_claim_ids": [claim_id],
            "chart": {
                "chart_type": "bar",
                "title": f"{metric} differs by category",
                "x_field": case["dimension"],
                "series": [
                    {
                        "field": metric,
                        "label": metric,
                        "metric": metric,
                        "format": "percent" if unit_family == "percentage" else "number",
                        "unit": unit,
                        "axis": "left",
                    }
                ],
                "source_caption": "Synthetic structured evidence",
                "show_labels": False,
                "supports_claim_ids": [claim_id],
            },
        }
        if index == 1 and case.get("first_presentation") == "table":
            artifact = {
                "artifact_path": path,
                "usage": "summary_table",
                "finding_ids": ["finding_1"],
                "purpose": f"Compare exact {metric} values by category",
                "evidence_role": "primary",
                "supports_claim_ids": [claim_id],
                "table": {
                    "title": f"{metric} exact comparison",
                    "columns": [
                        {"field": case["dimension"], "label": case["dimension"]},
                        {
                            "field": metric,
                            "label": metric,
                            "format": "number",
                            "unit": unit,
                            "metric": metric,
                        },
                    ],
                    "supports_claim_ids": [claim_id],
                },
            }
        artifacts.append(artifact)

    resolver.resolve(project["id"], "analysis/findings.json").write_text(
        json.dumps(
            {
                "summary": claims[0]["statement"],
                "findings": [
                    {
                        "id": "finding_1",
                        "title": claims[0]["statement"],
                        "evidence": ["Two independently computed metrics were verified"],
                        "risk": "The difference may require monitoring",
                        "recommendation": "Review the measured categories",
                        "related_artifacts": related,
                        "claims": claims,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    first_metric = case["metrics"][0]
    manifest = ReportEvidenceManifest.model_validate(
        {
            "schema_version": "1.0",
            "metrics": metrics,
            "kpis": [
                {
                    "id": "overview_value",
                    "label": "Overview value",
                    "metric": first_metric[0],
                    "artifact_path": "data/series_1.csv",
                    "selector": {
                        "type": "table",
                        "records_path": [],
                        "row": 0,
                        "field": first_metric[0],
                    },
                    "format": "percent" if first_metric[2] == "percentage" else "number",
                    "unit": first_metric[3],
                    "role": "overview",
                    "purpose": "Provide dataset scale context",
                    "evidence_role": "context",
                }
            ],
            "artifacts": artifacts,
        }
    )
    with client.app.state.database.session() as session:
        artifact_service = ArtifactService(session)
        for path in related:
            source = resolver.resolve(project["id"], path)
            artifact_service.register(project["id"], path, source.stat().st_size)
        findings_path = resolver.resolve(project["id"], "analysis/findings.json")
        artifact_service.register(
            project["id"], "analysis/findings.json", findings_path.stat().st_size
        )
        ReportEvidenceDeclarationService(session, resolver).declare(project["id"], manifest)
    return project, resolver


@pytest.mark.parametrize("case", DATASETS, ids=[item["name"] for item in DATASETS])
def test_same_semantic_pipeline_handles_unrelated_dataset_shapes(client, settings, case) -> None:
    project, resolver = _prepare_generic_project(client, settings, case)
    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver, SkillLoader(settings.skill_root)).collect(
            project["id"], "Analyze", case["name"]
        )
    schemas = RunContextBuilder.artifact_schemas(
        resolver.project_root(project["id"]), "data/series_1.csv"
    )
    primary_schema = next(
        item for item in schemas if item["artifact_path"] == "data/series_1.csv"
    )["schema"]
    fallback = FallbackSpecBuilder(resolver).build(project["id"], inputs)
    spec = ReportPlanner(MockLLMProvider([]), resolver).validate_and_hydrate(
        project["id"], fallback, inputs
    )
    document = ReportRenderer(resolver).render(project["id"], spec)

    # Fallback no longer guesses charts/tables. A narrative report is valid.
    assert spec.storyline is None
    assert spec.title
    assert spec.kpis
    assert spec.sections
    assert spec.sections
    assert primary_schema["row_count"] == 2
    assert [column["name"] for column in primary_schema["columns"]] == [
        case["dimension"],
        case["metrics"][0][0],
    ]
    assert not any(
        token in document for token in ("成交金额", "借呗", "花呗", "华东", "华南", "销售人员")
    )
