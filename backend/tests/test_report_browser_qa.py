from datetime import date, timedelta

import pytest

from app.llm.mock import MockLLMProvider
from tests.test_report_browser_rendering import _measure
from tests.test_report_editor_pipeline import (
    _editor_spec,
    _generate,
    accept_report_ready_artifact,
    prepare_editor_project,
)


def _write_metrics(resolver, project_id, metrics: list[dict]) -> None:
    import json

    path = resolver.resolve(project_id, "analysis/metrics.json")
    existing = json.loads(path.read_text(encoding="utf-8")).get("metrics", [])
    by_id = {item["metric_id"]: item for item in existing}
    by_id.update({item["metric_id"]: item for item in metrics})
    path.write_text(json.dumps({"metrics": list(by_id.values())}), encoding="utf-8")


def _count_metric(metric_id: str, path: str) -> dict:
    return {
        "metric_id": metric_id,
        "label": metric_id.replace("_", " ").title(),
        "value": 12,
        "aggregation": "sum",
        "semantic_type": "count",
        "unit_family": "count",
        "unit": "items",
        "definition": f"Verified {metric_id}",
        "source_artifact": path,
        "count_semantics": "field_sum",
        "is_distinct": False,
    }


def _amount_metric(metric_id: str, path: str, value: float = 24475175.8) -> dict:
    return {
        "metric_id": metric_id,
        "label": metric_id.replace("_", " ").title(),
        "value": value,
        "aggregation": "sum",
        "semantic_type": "measure",
        "unit_family": "currency",
        "unit": "yuan",
        "definition": f"Verified {metric_id}",
        "source_artifact": path,
    }


@pytest.mark.asyncio
async def test_browser_long_series_keeps_points_and_source_gap(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True)
    start = date(2020, 4, 1)
    rows = ["period,amount"]
    rows.extend(f"{start + timedelta(days=index)},{10000 + index}" for index in range(50))
    resolver.resolve(project["id"], "data/summary.csv").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )
    _write_metrics(resolver, project["id"], [_amount_metric("amount", "data/summary.csv")])
    accept_report_ready_artifact(
        client,
        resolver,
        project["id"],
        artifact_path="data/summary.csv",
        dimension="period",
        measures={"amount": "amount"},
    )
    draft = _editor_spec(kpis=[])
    draft["sections"][0]["lead"] = None
    draft["sections"][0]["blocks"] = [
        {
            "type": "narrative",
            "text": "The full series is retained.",
            "purpose": "Lead",
        },
        {
            "type": "chart",
            "data_ref": "data/summary.csv",
            "chart_type": "line",
            "x_field": "period",
            "series": ["amount"],
            "title": "Long series",
            "purpose": "Trend",
        },
        {"type": "callout", "tone": "note", "text": "Source should sit under the chart."},
    ]
    path = await _generate(client, settings, project, resolver, MockLLMProvider([draft]))
    rendered = _measure(resolver.resolve(project["id"], path))
    assert rendered["runtimeErrors"] == []
    assert rendered["dataPointCount"] == 50
    assert rendered["xTickCount"] < 15
    assert rendered["overflowCount"] == 0
    assert rendered["sourceGap"] is not None
    assert 0 <= rendered["sourceGap"] <= 40
    assert abs(rendered["chartLeft"] - rendered["narrativeLeft"]) <= 2
    assert abs(rendered["calloutLeft"] - rendered["narrativeLeft"]) <= 2


@pytest.mark.asyncio
async def test_browser_mixed_amount_and_count_split_into_two_charts(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True)
    resolver.resolve(project["id"], "data/summary.csv").write_text(
        "category,amount,orders\nA,24475175,12\nB,18000000,9\n",
        encoding="utf-8",
    )
    _write_metrics(
        resolver,
        project["id"],
        [
            _amount_metric("amount", "data/summary.csv"),
            _count_metric("orders", "data/summary.csv"),
        ],
    )
    accept_report_ready_artifact(
        client,
        resolver,
        project["id"],
        artifact_path="data/summary.csv",
        dimension="category",
        measures={"amount": "amount", "orders": "orders"},
    )
    draft = _editor_spec(kpis=[])
    draft["sections"][0]["lead"] = None
    draft["sections"][0]["blocks"] = [
        {
            "type": "chart",
            "data_ref": "data/summary.csv",
            "chart_type": "line",
            "x_field": "category",
            "series": ["amount", "orders"],
            "title": "Mixed families",
            "purpose": "Do not share one axis",
        }
    ]
    path = await _generate(client, settings, project, resolver, MockLLMProvider([draft]))
    rendered = _measure(resolver.resolve(project["id"], path))
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert rendered["chartCount"] == 2
    assert "axis-incompatible" not in html
    assert rendered["overflowCount"] == 0


@pytest.mark.asyncio
async def test_browser_wide_table_is_appendix_and_keeps_columns(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True)
    header = ",".join(["category"] + [f"m{index}" for index in range(8)])
    row = ",".join(["A"] + [str(index) for index in range(8)])
    resolver.resolve(project["id"], "data/summary.csv").write_text(
        f"{header}\n{row}\n", encoding="utf-8"
    )
    accept_report_ready_artifact(
        client,
        resolver,
        project["id"],
        artifact_path="data/summary.csv",
        dimension="category",
        measures={f"m{index}": f"m{index}" for index in range(8)},
    )
    draft = _editor_spec(kpis=[])
    draft["sections"][0]["lead"] = None
    draft["sections"][0]["blocks"] = [
        {
            "type": "table",
            "data_ref": "data/summary.csv",
            "columns": ["category"] + [f"m{index}" for index in range(8)],
            "title": "Wide table",
            "purpose": "Density",
        }
    ]
    path = await _generate(client, settings, project, resolver, MockLLMProvider([draft]))
    rendered = _measure(resolver.resolve(project["id"], path))
    assert rendered["tableUsage"] == "appendix"
    assert rendered["tableColumnCount"] == 9


@pytest.mark.asyncio
async def test_browser_dense_visual_group_stacks(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=True)
    rows = ["category,amount"]
    rows.extend(f"long-category-label-{index},{index}" for index in range(16))
    resolver.resolve(project["id"], "data/summary.csv").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )
    _write_metrics(resolver, project["id"], [_amount_metric("amount", "data/summary.csv")])
    accept_report_ready_artifact(
        client,
        resolver,
        project["id"],
        artifact_path="data/summary.csv",
        dimension="category",
        measures={"amount": "amount"},
    )
    draft = _editor_spec(kpis=[])
    draft["sections"][0]["lead"] = None
    draft["sections"][0]["blocks"] = [
        {
            "type": "visual_group",
            "layout": "two-column",
            "items": [
                {
                    "type": "chart",
                    "data_ref": "data/summary.csv",
                    "chart_type": "line",
                    "x_field": "category",
                    "series": ["amount"],
                    "title": "Dense A",
                    "purpose": "Left",
                },
                {
                    "type": "chart",
                    "data_ref": "data/summary.csv",
                    "chart_type": "bar",
                    "x_field": "category",
                    "series": ["amount"],
                    "title": "Dense B",
                    "purpose": "Right",
                },
            ],
        },
        {
            "type": "narrative",
            "text": "The two views show the same measured pattern at different densities.",
            "claim_ids": ["claim_total"],
            "purpose": "Interpret the grouped evidence",
            "display_role": "evidence_interpretation",
            "related_block_id": "data/summary.csv",
            "metric_refs": ["amount"],
        },
    ]
    path = await _generate(client, settings, project, resolver, MockLLMProvider([draft]))
    rendered = _measure(resolver.resolve(project["id"], path))
    assert "visual-group-stack" in rendered["visualGroupClass"]
    assert "visual-group-two-column" not in rendered["visualGroupClass"]
