import json

import pytest
from sqlalchemy import select

from app.core.errors import AppError, LLMError
from app.llm.mock import MockLLMProvider
from app.models import Artifact
from app.services.artifacts import ArtifactService
from app.services.reports import ReportService
from app.services.workspace import PathResolver
from app.skills.loader import SkillLoader

SAFE_HTML = (
    "<!doctype html><html><head><style>html,body{background:#fff}"
    "body{max-width:1200px;margin:0 auto;padding:40px 48px}</style>"
    "</head><body><h1>Report</h1><svg></svg></body></html>"
)


def prepare_report_inputs(client, settings):
    project = client.post("/api/projects", json={"name": "Report"}).json()
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("sales.csv", "region,sales\nEast,100\n", "text/csv")},
    )
    resolver = PathResolver(settings.workspace_root)
    resolver.resolve(project["id"], "plans/analysis_plan.json").write_text(
        json.dumps({"action": "create_plan", "title": "Plan", "objective": "Analyze", "tasks": []})
    )
    resolver.resolve(project["id"], "data/evidence.csv").write_text("region,sales\nEast,100\n")
    resolver.resolve(project["id"], "analysis/findings.json").write_text(
        json.dumps(
            {
                "summary": "East leads",
                "findings": [
                    {
                        "id": "finding_1",
                        "title": "East leads",
                        "evidence": ["evidence.csv East=100"],
                        "risk": "Concentration",
                        "recommendation": "Diversify",
                        "related_artifacts": ["data/evidence.csv"],
                        "claims": [
                            {
                                "claim_id": "claim_total",
                                "statement": "The measured total is 100",
                                "priority": "primary",
                                "strength": 0.9,
                                "evidence_metric_ids": ["total_sales"],
                            },
                            {
                                "claim_id": "claim_east",
                                "statement": "East has the measured category value",
                                "priority": "secondary",
                                "strength": 0.7,
                                "evidence_metric_ids": ["sales"],
                            },
                        ],
                    }
                ],
            }
        )
    )
    resolver.resolve(project["id"], "analysis/report_evidence.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "metrics": [
                    {
                        "metric_id": "total_sales",
                        "label": "Total value",
                        "value": 100,
                        "aggregation": "sum",
                        "semantic_type": "measure",
                        "unit_family": "currency",
                        "unit": "元",
                        "definition": "Sum of measured values",
                        "source_artifact": "data/evidence.csv",
                    },
                    {
                        "metric_id": "sales",
                        "label": "Category value",
                        "value": 100,
                        "aggregation": "sum",
                        "semantic_type": "measure",
                        "unit_family": "currency",
                        "unit": "元",
                        "definition": "Measured value by category",
                        "source_artifact": "data/evidence.csv",
                    },
                ],
                "kpis": [
                    {
                        "id": "total_sales",
                        "label": "累计销售额",
                        "metric": "total_sales",
                        "artifact_path": "data/evidence.csv",
                        "selector": {
                            "type": "table",
                            "records_path": [],
                            "row": 0,
                            "field": "sales",
                        },
                        "format": "currency",
                        "decimals": 0,
                        "unit": "元",
                        "finding_ids": ["finding_1"],
                        "purpose": "量化核心销售结果",
                        "role": "evidence",
                        "presentation_roles": ["overview", "evidence"],
                        "evidence_role": "primary",
                        "supports_claim_ids": ["claim_total"],
                    }
                ],
                "artifacts": [
                    {
                        "artifact_path": "data/evidence.csv",
                        "usage": "visual_source",
                        "finding_ids": ["finding_1"],
                        "purpose": "证明东区销售结果领先",
                        "evidence_role": "primary",
                        "supports_claim_ids": ["claim_east"],
                        "chart": {
                            "chart_type": "bar",
                            "title": "区域销售额",
                            "x_field": "region",
                            "series": [
                                {
                                    "field": "sales",
                                    "label": "销售额",
                                    "metric": "sales",
                                    "format": "currency",
                                    "decimals": 0,
                                    "unit": "元",
                                }
                            ],
                            "source_caption": "来源：data/evidence.csv",
                            "show_labels": False,
                            "supports_claim_ids": ["claim_east"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project, resolver


@pytest.mark.asyncio
async def test_report_service_uses_bounded_inputs_and_registers_artifact(client, settings) -> None:
    project, resolver = prepare_report_inputs(client, settings)
    provider = MockLLMProvider([])
    with client.app.state.database.session() as session:
        ArtifactService(session).register(project["id"], "data/evidence.csv", 27)
        path = await ReportService(
            session,
            resolver,
            SkillLoader(settings.skill_root),
            provider,
        ).generate(project["id"], "Analyze sales", "Sales report")
        artifact = session.scalar(
            select(Artifact).where(Artifact.project_id == project["id"], Artifact.path == path)
        )

    assert path == "reports/report.html"
    assert artifact.artifact_type == "report"
    request_payload = provider.requests[0][-1]["content"]
    assert "findings" in request_payload
    assert "artifact_catalog" in request_payload
    assert "metrics" in request_payload
    assert "dataset_summary" in request_payload
    assert "kpi_menu" not in request_payload
    assert "evidence_menu" not in request_payload
    assert "stdout" not in request_payload
    assert "conversation" not in request_payload
    assert "data-analysis" not in provider.requests[0][0]["content"]


@pytest.mark.asyncio
async def test_report_service_extracts_html_from_markdown_wrapper(client, settings) -> None:
    assert (
        ReportService.extract_html(f"Here is the report:\n```html\n{SAFE_HTML}\n```") == SAFE_HTML
    )


@pytest.mark.asyncio
async def test_report_service_retries_rejected_model_output(client, settings) -> None:
    project, resolver = prepare_report_inputs(client, settings)
    provider = MockLLMProvider(["markdown instead of html"])
    with client.app.state.database.session() as session:
        path = await ReportService(
            session,
            resolver,
            SkillLoader(settings.skill_root),
            provider,
        ).generate(project["id"], "Analyze sales", "Sales report")

    assert (
        resolver.resolve(project["id"], path)
        .read_text(encoding="utf-8")
        .startswith("<!doctype html>")
    )
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_report_service_uses_safe_local_fallback_after_retries(client, settings) -> None:
    project, resolver = prepare_report_inputs(client, settings)
    provider = MockLLMProvider(["not html", "still not html", "again not html"])
    with client.app.state.database.session() as session:
        path = await ReportService(
            session,
            resolver,
            SkillLoader(settings.skill_root),
            provider,
        ).generate(project["id"], "Analyze sales", "Sales report")

    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "The measured total is 100" in html
    assert "East leads" in html
    assert "evidence.csv East=100" in html
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_report_service_uses_local_fallback_when_model_is_unavailable(
    client, settings
) -> None:
    project, resolver = prepare_report_inputs(client, settings)
    provider = MockLLMProvider([])

    async def unavailable(messages: list[dict[str, str]]) -> str:
        provider.requests.append(messages)
        raise LLMError("Model service is temporarily unavailable", "llm_unavailable")

    provider.text_chat = unavailable
    with client.app.state.database.session() as session:
        path = await ReportService(
            session,
            resolver,
            SkillLoader(settings.skill_root),
            provider,
        ).generate(project["id"], "Analyze sales", "Sales report")
        artifact = session.scalar(
            select(Artifact).where(Artifact.project_id == project["id"], Artifact.path == path)
        )

    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert path == "reports/report.html"
    assert html.startswith("<!doctype html>")
    assert "The measured total is 100" in html
    assert "East leads" in html
    assert artifact is not None
    assert artifact.artifact_type == "report"
    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    "html",
    [
        "markdown instead of html",
        "<!doctype html><html><img src='https://example.com/a.png'></html>",
        "<!doctype html><html><script>document.cookie</script></html>",
        "<!doctype html><html><iframe src='data:text/html,x'></iframe></html>",
        "<!doctype html><html><head><meta http-equiv='refresh' content='0;url=https://example.com'></head></html>",
        "<!doctype html><html><style>body{background:url(https://example.com/a.png)}</style></html>",
        "<!doctype html><html><script>fetch ('https://example.com')</script></html>",
    ],
)
def test_report_html_validator_rejects_unsafe_or_invalid_documents(html: str) -> None:
    with pytest.raises(AppError):
        ReportService.validate_html(html)


def test_report_html_validation_no_longer_scans_evidence_numbers() -> None:
    # Replaced the old Evidence-contract number scan. Renderer HTML validation
    # stays structural/safety-only; KPI values come from metric_ref.
    html = "<!doctype html><html><body><p>Revenue increased 999%</p></body></html>"
    ReportService.validate_html(html, {"revenue_growth": "12%"})


def test_report_html_extractor_rejects_incomplete_document() -> None:
    with pytest.raises(AppError, match="complete HTML"):
        ReportService.extract_html("```html\n<html><body>cut off")


def test_report_content_response_has_restrictive_csp(client, settings) -> None:
    project, resolver = prepare_report_inputs(client, settings)
    resolver.resolve(project["id"], "reports/report.html").write_text(SAFE_HTML)

    response = client.get(f"/api/projects/{project['id']}/files/reports/report.html/content")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store, max-age=0"
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "connect-src 'none'" in csp
    assert "navigate-to 'none'" in csp
    assert "frame-ancestors 'self'" in csp
    assert all(origin in csp for origin in settings.frontend_origins)


def test_html_preview_exposes_file_revision(client, settings) -> None:
    project, resolver = prepare_report_inputs(client, settings)
    resolver.resolve(project["id"], "reports/report.html").write_text(SAFE_HTML)
    response = client.get(f"/api/projects/{project['id']}/files/reports/report.html")
    assert response.status_code == 200
    assert response.json()["revision"]
