"""New Report Editor main path. Old Readiness/Evidence tests are not the contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.orchestrator import AnalysisOrchestrator
from app.core.config import PROJECT_ROOT
from app.llm.mock import MockLLMProvider
from app.models import AnalysisRun
from app.sandbox.executor import SandboxExecutor
from app.services.artifacts import ArtifactService
from app.services.report_editor_prompt import ReportEditorPromptLoader
from app.services.report_editor_spec import ReportEditorCalloutBlock, ReportEditorSpec
from app.services.report_inputs import ReportInputCollector
from app.services.report_ready_artifacts import ReportReadyArtifact
from app.services.report_validator import ReportSpecValidator
from app.services.reports import ReportService
from app.services.workspace import PathResolver
from app.skills.loader import SkillLoader, SkillStage
from tests.test_orchestrator import COMPLETE, EXECUTE, PLAN, FakeExecutor, prepare_run


def _metric(metric_id: str, value: float, path: str, unit: str = "yuan") -> dict:
    return {
        "metric_id": metric_id,
        "label": metric_id.replace("_", " ").title(),
        "value": value,
        "aggregation": "sum",
        "semantic_type": "measure",
        "unit_family": "currency",
        "unit": unit,
        "definition": f"Verified {metric_id}",
        "source_artifact": path,
    }


def _findings(related: list[str] | None = None) -> dict:
    return {
        "summary": "East leads the measured total.",
        "findings": [
            {
                "id": "finding_1",
                "title": "East leads",
                "evidence": ["East has the measured value 100"],
                "risk": "Concentration",
                "recommendation": "Watch other categories",
                "related_artifacts": related or [],
                "claims": [
                    {
                        "claim_id": "claim_total",
                        "statement": "The measured total is 100",
                        "priority": "primary",
                        "strength": 0.9,
                        "evidence_metric_ids": ["total_sales"],
                    }
                ],
            }
        ],
    }


def _editor_spec(**overrides) -> dict:
    spec = {
        "headline": "Measured total stays concentrated in one category",
        "summary": "The measured total is 100. Concentration is the main risk, not missing charts.",
        "kpis": [
            {
                "metric_ref": "total_sales",
                "display_label": "Total value",
                "purpose": "Show overall scale",
            }
        ],
        "sections": [
            {
                "title": "One category holds the measured total",
                "lead": "East accounts for the verified total of 100.",
                "finding_refs": ["finding_1"],
                "claim_ids": ["claim_total"],
                "layout": "flow",
                "blocks": [
                    {
                        "type": "narrative",
                        "text": "East accounts for the verified total of 100.",
                        "claim_ids": ["claim_total"],
                        "purpose": "Explain the measured result",
                    }
                ],
            }
        ],
    }
    spec.update(overrides)
    return spec


def prepare_editor_project(
    client,
    settings,
    *,
    with_chart: bool = True,
    with_evidence: bool = False,
    report_ready: bool = False,
):
    project = client.post("/api/projects", json={"name": "Editor"}).json()
    client.post(
        f"/api/projects/{project['id']}/files",
        files={"file": ("source.csv", "region,sales\nEast,100\n", "text/csv")},
    )
    resolver = PathResolver(settings.workspace_root)
    resolver.resolve(project["id"], "plans/analysis_plan.json").write_text(
        json.dumps(
            {
                "analysis_topic": "Category performance",
                "objective": "Explain concentration",
            }
        ),
        encoding="utf-8",
    )
    related: list[str] = []
    if with_chart:
        data_path = resolver.resolve(project["id"], "data/summary.csv")
        data_path.parent.mkdir(parents=True, exist_ok=True)
        data_path.write_text("region,sales\nEast,100\nWest,40\n", encoding="utf-8")
        related = ["data/summary.csv"]
    metrics = [_metric("total_sales", 100, related[0] if related else "analysis/findings.json")]
    resolver.resolve(project["id"], "analysis/metrics.json").write_text(
        json.dumps({"metrics": metrics}), encoding="utf-8"
    )
    resolver.resolve(project["id"], "analysis/findings.json").write_text(
        json.dumps(_findings(related)), encoding="utf-8"
    )
    if with_evidence:
        resolver.resolve(project["id"], "analysis/report_evidence.json").write_text(
            json.dumps({"schema_version": "1.0", "metrics": metrics, "kpis": [], "artifacts": []}),
            encoding="utf-8",
        )
    with client.app.state.database.session() as session:
        artifacts = ArtifactService(session)
        for path in ["analysis/findings.json", *related]:
            target = resolver.resolve(project["id"], path)
            artifacts.register(project["id"], path, target.stat().st_size)
        if report_ready and related:
            artifacts.replace_report_schemas(
                project["id"],
                [
                    ReportReadyArtifact.model_validate(
                        {
                            "artifact_path": "data/summary.csv",
                            "fields": [
                                {"name": "region", "role": "dimension"},
                                {
                                    "name": "sales",
                                    "role": "measure",
                                    "metric_ref": "total_sales",
                                },
                            ],
                        }
                    )
                ],
            )
    return project, resolver


def accept_report_ready_artifact(
    client,
    resolver,
    project_id: str,
    *,
    artifact_path: str,
    dimension: str,
    measures: dict[str, str],
    metric_definitions: list[dict] | None = None,
) -> None:
    metrics_path = resolver.resolve(project_id, "analysis/metrics.json")
    payload = (
        json.loads(metrics_path.read_text(encoding="utf-8"))
        if metrics_path.is_file()
        else {"metrics": []}
    )
    by_id = {item["metric_id"]: item for item in payload.get("metrics", [])}
    by_id.update({item["metric_id"]: item for item in (metric_definitions or [])})
    for field, metric_id in measures.items():
        definition = by_id.get(metric_id) or {
            "metric_id": metric_id,
            "label": metric_id.replace("_", " ").title(),
            "value": 1,
            "aggregation": "sum",
            "semantic_type": "measure",
            "unit_family": "quantity",
            "definition": f"Verified {metric_id} by {dimension}",
        }
        definition.update(
            {
                "metric_scope": "reusable_measure",
                "source_artifact": artifact_path,
                "source_field": field,
                "grain": definition.get("grain") or "dimension_row",
            }
        )
        by_id[metric_id] = definition
    metrics_path.write_text(json.dumps({"metrics": list(by_id.values())}), encoding="utf-8")
    target = resolver.resolve(project_id, artifact_path)
    with client.app.state.database.session() as session:
        artifacts = ArtifactService(session)
        artifacts.register(project_id, artifact_path, target.stat().st_size)
        artifacts.replace_report_schemas(
            project_id,
            [
                ReportReadyArtifact.model_validate(
                    {
                        "artifact_path": artifact_path,
                        "fields": [
                            {"name": dimension, "role": "dimension"},
                            *[
                                {
                                    "name": field,
                                    "role": "measure",
                                    "metric_ref": metric_id,
                                }
                                for field, metric_id in measures.items()
                            ],
                        ],
                    }
                )
            ],
        )


async def _generate(client, settings, project, resolver, provider):
    with client.app.state.database.session() as session:
        return await ReportService(
            session, resolver, SkillLoader(settings.skill_root), provider
        ).generate(project["id"], "Analyze", "Category performance")


@pytest.mark.asyncio
async def test_report_editor_loads_project_prompt_not_da_skill(
    client, settings, monkeypatch
) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    seen: list[str] = []
    original = SkillLoader.load

    def spy(self, stage, include_ad_analytics=False):
        seen.append(str(stage))
        return original(self, stage, include_ad_analytics)

    monkeypatch.setattr(SkillLoader, "load", spy)
    provider = MockLLMProvider([_editor_spec()])
    await _generate(client, settings, project, resolver, provider)
    assert SkillStage.REPORT not in seen
    system_prompt = provider.requests[0][0]["content"]
    assert "数据分析报告编辑器" in system_prompt
    assert system_prompt == ReportEditorPromptLoader().load()
    assert "核心方法论：多专家深度分析" not in system_prompt


@pytest.mark.asyncio
async def test_report_generation_does_not_execute_python(client, settings, monkeypatch) -> None:
    project, resolver = prepare_editor_project(client, settings)
    calls = {"count": 0}

    async def forbidden(self, *args, **kwargs):
        calls["count"] += 1
        raise AssertionError("Report generation must not execute Python")

    monkeypatch.setattr(SandboxExecutor, "execute", forbidden)
    path = await _generate(client, settings, project, resolver, MockLLMProvider([_editor_spec()]))
    assert path == "reports/report.html"
    assert calls["count"] == 0


@pytest.mark.asyncio
async def test_report_generates_without_report_evidence_json(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_evidence=False)
    assert not resolver.resolve(project["id"], "analysis/report_evidence.json").is_file()
    path = await _generate(client, settings, project, resolver, MockLLMProvider([_editor_spec()]))
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    spec = json.loads(
        resolver.resolve(project["id"], "reports/report_spec.json").read_text(encoding="utf-8")
    )
    assert html.startswith("<!doctype html>")
    assert spec["provenance"]["planner_mode"] == "llm"


@pytest.mark.asyncio
async def test_report_is_valid_without_charts(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    path = await _generate(client, settings, project, resolver, MockLLMProvider([_editor_spec()]))
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert "class='chart-card'" not in html
    assert "Report is not ready" not in html
    assert "100" in html


@pytest.mark.asyncio
async def test_ordered_blocks_render_as_editorial_flow_without_finding_ui(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True, report_ready=True)
    draft = _editor_spec()
    draft["sections"][0]["blocks"] = [
        {
            "type": "narrative",
            "text": "East explains the measured result.",
            "purpose": "Internal narrative purpose",
        },
        {
            "type": "chart",
            "data_ref": "data/summary.csv",
            "chart_type": "bar",
            "x_field": "region",
            "series": ["sales"],
            "title": "Regional sales",
            "purpose": "Internal purpose that must stay metadata",
        },
        {
            "type": "callout",
            "tone": "risk",
            "title": "需要关注",
            "text": "Concentration remains the main risk.",
        },
    ]
    draft["sections"][0]["layout"] = "flow"
    path = await _generate(client, settings, project, resolver, MockLLMProvider([draft]))
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")

    assert "layout-flow" in html
    assert "layout-split" not in html and "layout-grid" not in html
    assert "finding-narrative" not in html
    assert "数据证据" not in html
    assert "可验证结论" not in html
    assert "风险提示" not in html
    assert "Internal purpose that must stay metadata" not in html
    assert html.index("East explains the measured result.") < html.index("Regional sales")
    assert html.index("Regional sales") < html.index("Concentration remains the main risk.")


@pytest.mark.asyncio
async def test_visual_group_keeps_surrounding_narratives_in_document_flow(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True, report_ready=True)
    draft = _editor_spec()
    draft["sections"][0]["layout"] = "flow"
    draft["sections"][0]["blocks"] = [
        {
            "type": "narrative",
            "text": "The opening narrative stays in the reading flow.",
            "purpose": "Lead",
        },
        {
            "type": "visual_group",
            "layout": "two-column",
            "items": [
                {
                    "type": "chart",
                    "data_ref": "data/summary.csv",
                    "chart_type": "bar",
                    "x_field": "region",
                    "series": ["sales"],
                    "title": "Grouped chart A",
                    "purpose": "Left visual",
                },
                {
                    "type": "chart",
                    "data_ref": "data/summary.csv",
                    "chart_type": "bar",
                    "x_field": "region",
                    "series": ["sales"],
                    "title": "Grouped chart B",
                    "purpose": "Right visual",
                },
            ],
        },
        {
            "type": "narrative",
            "text": "The closing narrative returns to the reading flow.",
            "purpose": "Interpretation",
            "display_role": "evidence_interpretation",
            "related_block_id": "data/summary.csv",
        },
    ]
    path = await _generate(client, settings, project, resolver, MockLLMProvider([draft]))
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    opening = html.index("The opening narrative stays in the reading flow.")
    group_start = html.index("visual-group visual-group-two-column")
    chart_a = html.index("Grouped chart A")
    chart_b = html.index("Grouped chart B")
    closing = html.index("The closing narrative returns to the reading flow.")
    assert opening < group_start < chart_a < chart_b < closing
    assert html.count("class='chart-card wide-visual'") == 2
    assert "layout-two-column .blocks{display:grid" not in html
    assert "class='visual-group visual-group-two-column wide-visual'" in html


@pytest.mark.asyncio
async def test_table_uses_semantic_formatting_and_preview_cap(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True)
    rows = ["entity_id,share,value_per_active_day"]
    rows.append("1000003926,0.4665,121.24")
    rows.extend(f"{1000003927 + index},0.2,10" for index in range(15))
    resolver.resolve(project["id"], "data/summary.csv").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )
    metrics = [
        _metric("total_sales", 100, "data/summary.csv"),
        {
            "metric_id": "share_metric",
            "metric_scope": "reusable_measure",
            "label": "Share",
            "value": 0.4665,
            "aggregation": "mean",
            "semantic_type": "rate",
            "unit_family": "percentage",
            "ratio_basis": "fraction",
            "definition": "Mean share by entity",
            "source_artifact": "data/summary.csv",
            "source_field": "share",
            "grain": "entity_row",
        },
        {
            "metric_id": "active_day_value",
            "metric_scope": "reusable_measure",
            "label": "Value per active day",
            "value": 121.24,
            "aggregation": "mean",
            "semantic_type": "measure",
            "unit_family": "currency",
            "unit": "yuan",
            "definition": "Mean value per active day by entity",
            "source_artifact": "data/summary.csv",
            "source_field": "value_per_active_day",
            "grain": "entity_row",
        },
    ]
    resolver.resolve(project["id"], "analysis/metrics.json").write_text(
        json.dumps({"metrics": metrics}), encoding="utf-8"
    )
    with client.app.state.database.session() as session:
        artifacts = ArtifactService(session)
        target = resolver.resolve(project["id"], "data/summary.csv")
        artifacts.register(project["id"], "data/summary.csv", target.stat().st_size)
        artifacts.replace_report_schemas(
            project["id"],
            [
                ReportReadyArtifact.model_validate(
                    {
                        "artifact_path": "data/summary.csv",
                        "fields": [
                            {"name": "entity_id", "role": "dimension"},
                            {
                                "name": "share",
                                "role": "measure",
                                "metric_ref": "share_metric",
                            },
                            {
                                "name": "value_per_active_day",
                                "role": "measure",
                                "metric_ref": "active_day_value",
                            },
                        ],
                    }
                )
            ],
        )
    draft = _editor_spec()
    draft["sections"][0]["blocks"] = [
        {
            "type": "table",
            "data_ref": "data/summary.csv",
            "columns": ["entity_id", "share", "value_per_active_day"],
            "title": "Preview table",
            "purpose": "Show formatted values",
        }
    ]
    path = await _generate(client, settings, project, resolver, MockLLMProvider([draft]))
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert "1000003926" in html
    assert "1,000,003,926" not in html
    assert "1000003926.0" not in html
    assert "46.65%" in html
    assert ">0.4665<" not in html
    assert "Value Per Active Day" in html
    assert ">value_per_active_day<" not in html
    assert "其余内容未在正文展开" in html


@pytest.mark.asyncio
async def test_invalid_chart_field_is_dropped_without_python_repair(
    client, settings, monkeypatch
) -> None:
    project, resolver = prepare_editor_project(client, settings, report_ready=True)
    calls = {"count": 0}

    async def forbidden(self, *args, **kwargs):
        calls["count"] += 1
        raise AssertionError("invalid chart must not start Python repair")

    monkeypatch.setattr(SandboxExecutor, "execute", forbidden)
    invalid = _editor_spec()
    invalid["sections"][0]["blocks"].append(
        {
            "type": "chart",
            "data_ref": "data/summary.csv",
            "chart_type": "bar",
            "x_field": "does_not_exist",
            "series": ["sales"],
            "title": "Broken chart",
            "purpose": "Broken chart",
        }
    )
    repaired = _editor_spec()
    repaired["sections"][0]["section_role"] = "chart_led"
    repaired["sections"][0]["blocks"].extend(
        [
            {
                "type": "chart",
                "data_ref": "data/summary.csv",
                "chart_type": "bar",
                "x_field": "region",
                "series": ["sales"],
                "title": "Regional sales",
                "purpose": "Show the accepted comparison",
            },
            {
                "type": "narrative",
                "text": "The accepted comparison shows the measured category difference.",
                "purpose": "Interpret the accepted chart",
                "display_role": "evidence_interpretation",
                "related_block_id": "data/summary.csv",
                "metric_refs": ["sales"],
            },
        ]
    )
    provider = MockLLMProvider([invalid, repaired])
    await _generate(client, settings, project, resolver, provider)
    assert calls["count"] == 0
    assert len(provider.requests) == 2
    retry_payload = json.loads(provider.requests[1][-1]["content"])
    assert retry_payload["issues"][0]["code"] == "chart.unknown_field"


def test_unknown_kpi_metric_is_rejected_and_not_guessed(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver).collect(
            project["id"], "Analyze", "Category performance"
        )
    draft = ReportEditorSpec.model_validate(
        _editor_spec(
            kpis=[
                {
                    "metric_ref": "invented_metric",
                    "display_label": "Invented",
                    "purpose": "Should be rejected",
                }
            ]
        )
    )
    result = ReportSpecValidator.validate(draft, inputs)
    assert any(issue.code == "kpi.unknown_metric" for issue in result.issues)
    assert result.spec.kpis == []


@pytest.mark.asyncio
async def test_editor_provider_failure_falls_back_instead_of_failing_run(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    path = await _generate(client, settings, project, resolver, MockLLMProvider([]))
    spec = json.loads(
        resolver.resolve(project["id"], "reports/report_spec.json").read_text(encoding="utf-8")
    )
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert spec["provenance"]["planner_mode"] == "fallback"
    assert html.startswith("<!doctype html>")


@pytest.mark.asyncio
async def test_regenerate_report_reuses_analysis_and_skips_python(client, settings) -> None:
    run_id = prepare_run(client, "Regenerate")
    provider = MockLLMProvider([PLAN, EXECUTE, COMPLETE, _editor_spec()])
    executor = FakeExecutor()
    orchestrator = AnalysisOrchestrator(client.app.state.database, settings, provider, executor)
    await orchestrator.run(run_id)
    python_after_analysis = 1
    editor_calls = sum(schema is ReportEditorSpec for schema in provider.schemas)
    project_id = _project_id(client, run_id)
    findings_path = settings.workspace_root / project_id / "analysis" / "findings.json"
    findings_before = findings_path.read_bytes()

    provider.responses.append(_editor_spec(headline="Regenerated concentration story"))
    await orchestrator.regenerate_report(run_id)

    assert findings_path.read_bytes() == findings_before
    assert sum(schema is ReportEditorSpec for schema in provider.schemas) > editor_calls
    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        assert run.status == "completed"
        assert run.state == "DONE"
        assert run.execution_count == python_after_analysis


@pytest.mark.asyncio
async def test_generic_datasets_generate_without_sales_fields(client, settings) -> None:
    cases = [
        ("service-operations", "issue_type,hours\nlogin,8\nbilling,5\n", "hours", 8),
        ("inventory-operations", "item_class,units\nA,120\nB,75\n", "units", 120),
    ]
    for name, csv_text, unit, value in cases:
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
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        resolver.resolve(project["id"], "analysis/metrics.json").write_text(
            json.dumps({"metrics": [_metric("leading_value", value, "data/summary.csv", unit)]}),
            encoding="utf-8",
        )
        path = await _generate(client, settings, project, resolver, MockLLMProvider([]))
        html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>")
        assert name in html or "verified result" in html or "leading" in html.lower()
        assert "成交金额" not in html
        assert "借呗" not in html
        assert "花呗" not in html
        assert "销售人员" not in html


def test_prompt_loader_fails_closed_without_da_skill_fallback(tmp_path: Path) -> None:
    loader = ReportEditorPromptLoader(tmp_path)
    with pytest.raises(Exception, match="missing"):
        loader.load()
    (tmp_path / "REPORT_EDITOR_SYSTEM_PROMPT.md").write_text("   ", encoding="utf-8")
    with pytest.raises(Exception, match="empty"):
        loader.load()
    assert (PROJECT_ROOT / "REPORT_EDITOR_SYSTEM_PROMPT.md").is_file()


def _project_id(client, run_id: str) -> str:
    with client.app.state.database.session() as session:
        return session.get(AnalysisRun, run_id).project_id


def test_consecutive_duplicate_narratives_are_flagged(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver).collect(
            project["id"], "Analyze", "Category performance"
        )
    draft = _editor_spec()
    draft["sections"][0]["lead"] = "Unique lead sentence about the measured total."
    draft["sections"][0]["blocks"] = [
        {
            "type": "narrative",
            "text": "The same conclusion is repeated with the measured total of 100.",
            "purpose": "First",
        },
        {
            "type": "narrative",
            "text": "The same conclusion is repeated with the measured total of 100.",
            "purpose": "Second",
        },
    ]
    result = ReportSpecValidator.validate(ReportEditorSpec.model_validate(draft), inputs)
    assert any(issue.code == "section.duplicate_narrative" for issue in result.issues)


def _callout_with_claim_ids() -> dict:
    draft = _editor_spec()
    draft["sections"][0]["blocks"].append(
        {
            "type": "callout",
            "tone": "risk",
            "title": "Need attention",
            "text": "Concentration remains the main risk.",
            "claim_ids": ["claim_total"],
        }
    )
    return draft


def test_callout_schema_forbids_claim_ids() -> None:
    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        ReportEditorCalloutBlock.model_validate(
            {
                "type": "callout",
                "tone": "insight",
                "title": "Note",
                "text": "Watch concentration",
                "claim_ids": ["claim_total"],
            }
        )


def test_prompt_documents_callout_field_contract() -> None:
    prompt = ReportEditorPromptLoader().load()
    assert "callout 只能包含" in prompt
    assert "不要把它迁移到其他 Block" in prompt
    assert "不要创造输入中不存在的 Claim" in prompt
    start = prompt.find('"type": "callout"')
    assert start != -1
    snippet = prompt[start : start + 180]
    assert "claim_ids" not in snippet


@pytest.mark.asyncio
async def test_editor_repairs_illegal_callout_claim_ids(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    repaired = _editor_spec()
    repaired["sections"][0]["blocks"].append(
        {
            "type": "callout",
            "tone": "risk",
            "title": "Need attention",
            "text": "Concentration remains the main risk.",
        }
    )
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
    assert "Concentration remains the main risk." in html
    assert html.startswith("<!doctype html>")


@pytest.mark.asyncio
async def test_editor_invalid_output_twice_uses_fallback_not_internal_error(
    client, settings
) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    path = await _generate(
        client,
        settings,
        project,
        resolver,
        MockLLMProvider([_callout_with_claim_ids(), _callout_with_claim_ids()]),
    )
    spec = json.loads(
        resolver.resolve(project["id"], "reports/report_spec.json").read_text(encoding="utf-8")
    )
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert spec["provenance"]["planner_mode"] == "fallback"
    assert html.startswith("<!doctype html>")
    assert "Analysis failed because of an internal error" not in html
