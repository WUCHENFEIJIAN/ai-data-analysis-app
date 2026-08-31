import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest
from test_reports import prepare_report_inputs

from app.core.config import PROJECT_ROOT
from app.llm.mock import MockLLMProvider
from app.services.report_fallback import FallbackSpecBuilder
from app.services.report_inputs import ReportInputCollector
from app.services.report_renderer import ReportRenderer
from app.services.report_spec import ReportSpec
from app.services.workspace import PathResolver
from app.skills.loader import SkillLoader
from tests.test_report_editor_pipeline import (
    _editor_spec,
    _generate,
    accept_report_ready_artifact,
    prepare_editor_project,
)


def _measure(report_path: Path) -> dict:
    frontend_root = Path(__file__).resolve().parents[2] / "frontend"
    result = subprocess.run(
        ["node", "e2e/assert-report-rendering.mjs", str(report_path)],
        cwd=frontend_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_generated_report_renders_correctly_in_browser(client, settings) -> None:
    project, resolver = prepare_report_inputs(client, settings)
    accept_report_ready_artifact(
        client,
        resolver,
        project["id"],
        artifact_path="data/evidence.csv",
        dimension="region",
        measures={"sales": "sales"},
    )
    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver, SkillLoader(settings.skill_root)).collect(
            project["id"], "Analyze", "Report"
        )
    spec = FallbackSpecBuilder(resolver).build(project["id"], inputs)
    report_path = resolver.resolve(project["id"], "reports/report.html")
    report_path.write_text(ReportRenderer(resolver).render(project["id"], spec), encoding="utf-8")
    rendered = _measure(report_path)
    assert rendered["runtimeErrors"] == []
    assert rendered["chartCount"] == rendered["nonEmptyCharts"] > 0
    assert len(set(rendered["tickLabels"])) > 1 or rendered["valueLabelCount"] > 0
    assert all(item["raw"] is not None for item in rendered["tickMetadata"])
    assert 0 < rendered["summaryWidth"] <= rendered["sectionWidth"]
    assert rendered["hasSectionTwoColumnGrid"] is False


@pytest.mark.asyncio
async def test_browser_line_axis_thinning_and_hidden_value_labels(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True)
    start = date(2020, 4, 1)
    rows = ["period,measure"]
    rows.extend(f"{start + timedelta(days=index)},{index + 1}" for index in range(50))
    resolver.resolve(project["id"], "data/summary.csv").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )
    accept_report_ready_artifact(
        client,
        resolver,
        project["id"],
        artifact_path="data/summary.csv",
        dimension="period",
        measures={"measure": "measure"},
    )
    draft = _editor_spec()
    draft["sections"][0]["lead"] = None
    draft["sections"][0]["blocks"] = [
        {
            "type": "narrative",
            "text": "The measured series remains complete.",
            "purpose": "Lead",
        },
        {
            "type": "chart",
            "data_ref": "data/summary.csv",
            "chart_type": "line",
            "x_field": "period",
            "series": ["measure"],
            "title": "Daily measure",
            "purpose": "Trend",
        },
    ]
    path = await _generate(client, settings, project, resolver, MockLLMProvider([draft]))
    rendered = _measure(resolver.resolve(project["id"], path))
    assert rendered["runtimeErrors"] == []
    assert rendered["dataPointCount"] == 50
    assert rendered["xTickCount"] < 15
    assert rendered["xTickRaw"][0] == "2020-04-01"
    assert rendered["xTickRaw"][-1] == "2020-05-20"
    assert rendered["valueLabelCount"] == 0


@pytest.mark.asyncio
async def test_browser_short_time_series_keeps_ticks(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True)
    rows = ["period,measure"]
    start = date(2020, 4, 1)
    rows.extend(f"{start + timedelta(days=index)},{index + 1}" for index in range(5))
    resolver.resolve(project["id"], "data/summary.csv").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )
    accept_report_ready_artifact(
        client,
        resolver,
        project["id"],
        artifact_path="data/summary.csv",
        dimension="period",
        measures={"measure": "measure"},
    )
    draft = _editor_spec()
    draft["sections"][0]["lead"] = None
    draft["sections"][0]["blocks"] = [
        {
            "type": "chart",
            "data_ref": "data/summary.csv",
            "chart_type": "line",
            "x_field": "period",
            "series": ["measure"],
            "title": "Short series",
            "purpose": "Trend",
        }
    ]
    path = await _generate(client, settings, project, resolver, MockLLMProvider([draft]))
    rendered = _measure(resolver.resolve(project["id"], path))
    assert rendered["xTickCount"] == 5
    assert rendered["xTickRaw"][0] == "2020-04-01"
    assert rendered["xTickRaw"][-1] == "2020-04-05"


@pytest.mark.asyncio
async def test_browser_table_identifier_percentage_and_display_label(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True)
    resolver.resolve(project["id"], "data/summary.csv").write_text(
        "entity_id,share,value_per_active_day\n1000003926,0.4665,121.24\n",
        encoding="utf-8",
    )
    accept_report_ready_artifact(
        client,
        resolver,
        project["id"],
        artifact_path="data/summary.csv",
        dimension="entity_id",
        measures={
            "share": "share_metric",
            "value_per_active_day": "active_day_value",
        },
        metric_definitions=[
            {
                "metric_id": "share_metric",
                "label": "Share",
                "value": 0.4665,
                "aggregation": "mean",
                "semantic_type": "rate",
                "unit_family": "percentage",
                "ratio_basis": "fraction",
                "definition": "Mean share by entity",
            },
            {
                "metric_id": "active_day_value",
                "label": "Value per active day",
                "value": 121.24,
                "aggregation": "mean",
                "semantic_type": "measure",
                "unit_family": "currency",
                "unit": "yuan",
                "definition": "Mean value per active day by entity",
            },
        ],
    )
    draft = _editor_spec()
    draft["sections"][0]["lead"] = None
    draft["sections"][0]["blocks"] = [
        {
            "type": "table",
            "data_ref": "data/summary.csv",
            "columns": ["entity_id", "share", "value_per_active_day"],
            "title": "Formatted table",
            "purpose": "Semantics",
        }
    ]
    path = await _generate(client, settings, project, resolver, MockLLMProvider([draft]))
    rendered = _measure(resolver.resolve(project["id"], path))
    text = rendered["tableText"]
    assert "1000003926" in text
    assert "1,000,003,926" not in text
    assert "46.65%" in text
    assert "Value Per Active Day" in text
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert ">value_per_active_day<" not in html


@pytest.mark.asyncio
async def test_browser_reading_width_is_narrower_than_chart(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True, report_ready=True)
    draft = _editor_spec()
    draft["sections"][0]["blocks"] = [
        {
            "type": "narrative",
            "text": "Reading column text sits on the shared left edge.",
            "purpose": "Lead",
        },
        {
            "type": "chart",
            "data_ref": "data/summary.csv",
            "chart_type": "bar",
            "x_field": "region",
            "series": ["sales"],
            "title": "Wide visual",
            "purpose": "Comparison",
        },
    ]
    path = await _generate(client, settings, project, resolver, MockLLMProvider([draft]))
    rendered = _measure(resolver.resolve(project["id"], path))
    assert rendered["narrativeWidth"] > 0
    assert abs(rendered["chartWidth"] - rendered["narrativeWidth"]) <= 2
    assert abs(rendered["chartLeft"] - rendered["narrativeLeft"]) <= 2


@pytest.mark.asyncio
async def test_browser_visual_group_only_pairs_grouped_items(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True, report_ready=True)
    draft = _editor_spec()
    draft["sections"][0]["lead"] = None
    draft["sections"][0]["blocks"] = [
        {
            "type": "narrative",
            "text": "Opening narrative stays full width.",
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
                    "title": "Grouped A",
                    "purpose": "Left",
                },
                {
                    "type": "chart",
                    "data_ref": "data/summary.csv",
                    "chart_type": "bar",
                    "x_field": "region",
                    "series": ["sales"],
                    "title": "Grouped B",
                    "purpose": "Right",
                },
            ],
        },
        {
            "type": "narrative",
            "text": "The grouped views show the same verified regional pattern.",
            "claim_ids": ["claim_total"],
            "purpose": "Interpret the grouped evidence",
            "display_role": "evidence_interpretation",
            "related_block_id": "data/summary.csv",
            "metric_refs": ["total_sales"],
        },
        {
            "type": "narrative",
            "text": "Closing narrative stays full width.",
            "purpose": "Close",
        },
    ]
    path = await _generate(client, settings, project, resolver, MockLLMProvider([draft]))
    rendered = _measure(resolver.resolve(project["id"], path))
    assert rendered["visualGroupItemCount"] == 2
    assert len(set(rendered["visualGroupTops"])) == 1
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    group = html.index("class='visual-group visual-group-two-column")
    assert html.index("Opening narrative stays full width.") < group
    assert group < html.index("Closing narrative stays full width.")


def test_sales_fixture_rerender_uses_layout_and_table_semantics() -> None:
    project_id = "pj_63efd5c722954d31bdfc2b1afba6a71b"
    workspace = PROJECT_ROOT / "workspaces"
    spec_path = workspace / project_id / "reports" / "report_spec.json"
    if not spec_path.is_file():
        pytest.skip("sales fixture workspace is not present")
    spec = ReportSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    html = ReportRenderer(PathResolver(workspace)).render(project_id, spec)
    assert "reading-measure" in html
    assert "wide-visual" in html
    assert "layout-two-column .blocks{display:grid" not in html
    assert "1000003926" in html
    assert "1,000,003,926" not in html
    assert "46.65%" in html
    assert ">value_per_active_day<" not in html
    assert "function buildXAxisTicks" in html
