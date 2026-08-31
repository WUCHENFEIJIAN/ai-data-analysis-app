from __future__ import annotations

import json
import shutil

import pytest

from app.core.config import PROJECT_ROOT
from app.llm.mock import MockLLMProvider
from app.services.artifacts import ArtifactService
from app.services.workspace import PathResolver
from tests.test_report_editor_pipeline import (
    _callout_with_claim_ids,
    _editor_spec,
    _generate,
    prepare_editor_project,
)

REAL_PROJECT = PROJECT_ROOT / "workspaces" / "pj_8992765aa89a4747b519135c83bfd678"
REAL_CLAIM_IDS = {
    "claim_overall_rate",
    "claim_payment_confirmation_gap",
    "claim_depth_gradient",
    "claim_depth_payment",
    "claim_source_quality",
    "claim_source_volume",
    "claim_market4",
    "claim_device_difference",
    "claim_new_user_share",
    "claim_new_user_source",
    "claim_quality_clean",
    "claim_duplicates",
}


def _copy_real_project(client, settings):
    if not REAL_PROJECT.exists():
        pytest.skip("real project workspace is not available")
    project = client.post("/api/projects", json={"name": "real-user-behavior"}).json()
    resolver = PathResolver(settings.workspace_root)
    copied: list[str] = []
    for relative in (
        "analysis/findings.json",
        "plans/analysis_plan.json",
        "context/dataset_profile.json",
        "data/user_behavior_funnel.csv",
        "data/user_behavior_segments.csv",
        "data/user_behavior_page_depth.csv",
        "data/user_behavior_summary.json",
        "data/user_behavior_quality.json",
        "data/user_behavior_combinations.csv",
    ):
        source = REAL_PROJECT / relative
        if not source.is_file():
            continue
        target = resolver.resolve(project["id"], relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative)
    with client.app.state.database.session() as session:
        artifacts = ArtifactService(session)
        for path in copied:
            target = resolver.resolve(project["id"], path)
            artifacts.register(project["id"], path, target.stat().st_size)
    return project, resolver


def _real_editor_spec() -> dict:
    return {
        "headline": "Payment confirmation stays low after users reach checkout",
        "summary": "Confirmation is about 1.90%. The payment drop is the main gap.",
        "kpis": [],
        "sections": [
            {
                "title": "Confirmation is the main drop",
                "finding_refs": ["finding_overall"],
                "claim_ids": ["claim_overall_rate", "claim_payment_confirmation_gap"],
                "layout": "flow",
                "blocks": [
                    {
                        "type": "narrative",
                        "text": "Users reach payment far more often than they confirm.",
                        "claim_ids": ["claim_overall_rate", "claim_payment_confirmation_gap"],
                        "purpose": "Lead finding",
                    },
                    {
                        "type": "chart",
                        "data_ref": "data/user_behavior_funnel.csv",
                        "chart_type": "bar",
                        "x_field": "stage_label",
                        "series": ["users"],
                        "title": "Funnel users by stage",
                        "purpose": "Show funnel drop",
                    },
                    {
                        "type": "callout",
                        "tone": "risk",
                        "title": "Confirmation gap",
                        "text": "The payment-to-confirmation drop needs attention.",
                    },
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_real_user_behavior_project_generates_report(client, settings) -> None:
    project, resolver = _copy_real_project(client, settings)
    path = await _generate(
        client, settings, project, resolver, MockLLMProvider([_real_editor_spec()])
    )
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    spec = json.loads(
        resolver.resolve(project["id"], "reports/report_spec.json").read_text(encoding="utf-8")
    )
    assert path == "reports/report.html"
    assert spec["provenance"]["planner_mode"] == "llm"
    assert html.startswith("<!doctype html>")
    assert "report-container" in html
    assert "Analysis failed because of an internal error" not in html
    assert "Funnel users by stage" in html
    assert "chart-card" in html
    bound = []
    for section in spec["sections"]:
        bound.extend(section.get("claim_ids") or [])
        for block in section["blocks"]:
            bound.extend(block.get("claim_ids") or [])
    assert bound
    assert set(bound) <= REAL_CLAIM_IDS


@pytest.mark.asyncio
async def test_real_user_behavior_project_fallback_without_metrics(client, settings) -> None:
    project, resolver = _copy_real_project(client, settings)
    assert not resolver.resolve(project["id"], "analysis/metrics.json").is_file()
    assert not resolver.resolve(project["id"], "analysis/report_evidence.json").is_file()
    path = await _generate(client, settings, project, resolver, MockLLMProvider([]))
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    spec = json.loads(
        resolver.resolve(project["id"], "reports/report_spec.json").read_text(encoding="utf-8")
    )
    assert spec["provenance"]["planner_mode"] == "fallback"
    assert html.startswith("<!doctype html>")
    assert "Analysis failed because of an internal error" not in html
    assert spec["kpis"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,csv_text,field",
    [
        ("ops", "issue_type,hours\nlogin,8\nbilling,5\n", "hours"),
        ("inventory", "item_class,units\nA,120\nB,75\n", "units"),
    ],
)
async def test_generic_field_names_generate_reports(
    client, settings, name, csv_text, field
) -> None:
    project = client.post("/api/projects", json={"name": name}).json()
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("source.csv", csv_text, "text/csv")},
    )
    resolver = PathResolver(settings.workspace_root)
    data_path = resolver.resolve(project["id"], "data/summary.csv")
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(csv_text, encoding="utf-8")
    resolver.resolve(project["id"], "analysis/findings.json").write_text(
        json.dumps(
            {
                "summary": f"{name} has a verified result.",
                "findings": [
                    {
                        "id": "finding_1",
                        "title": f"{name} result",
                        "evidence": ["summary.csv exists"],
                        "risk": "Limited sample",
                        "recommendation": "Review the leading class",
                        "related_artifacts": ["data/summary.csv"],
                        "claims": [
                            {
                                "claim_id": "claim_leading",
                                "statement": f"The leading {field} is verified",
                                "priority": "primary",
                                "strength": 0.8,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with client.app.state.database.session() as session:
        ArtifactService(session).register(
            project["id"], "data/summary.csv", data_path.stat().st_size
        )
        ArtifactService(session).register(
            project["id"],
            "analysis/findings.json",
            resolver.resolve(project["id"], "analysis/findings.json").stat().st_size,
        )
    path = await _generate(client, settings, project, resolver, MockLLMProvider([]))
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "成交金额" not in html
    assert "销售人员" not in html


@pytest.mark.asyncio
async def test_failure_chain_callout_unknown_claim_and_retries(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    repaired = _editor_spec()
    path = await _generate(
        client,
        settings,
        project,
        resolver,
        MockLLMProvider([_callout_with_claim_ids(), repaired]),
    )
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    spec = json.loads(
        resolver.resolve(project["id"], "reports/report_spec.json").read_text(encoding="utf-8")
    )
    assert spec["provenance"]["planner_mode"] == "llm"
    assert html.startswith("<!doctype html>")
    assert "Analysis failed because of an internal error" not in html
