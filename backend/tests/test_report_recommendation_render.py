"""Recommendation renderer uses vertical document flow and does not pad groups."""

from __future__ import annotations

from types import SimpleNamespace

from app.schemas.findings import Findings
from app.services.report_renderer import REPORT_DESIGN_TOKENS, ReportRenderer
from app.services.report_spec import RecommendationBlock, RecommendationItemSpec


def _findings() -> Findings:
    return Findings.model_validate(
        {
            "summary": "One class leads.",
            "findings": [
                {
                    "id": "finding_1",
                    "title": "One class leads",
                    "evidence": ["The leading class is verified."],
                    "risk": "Concentration",
                    "recommendation": "Review the other classes.",
                    "claims": [
                        {
                            "claim_id": "claim_1",
                            "statement": "The leading class is verified.",
                            "evidence_metric_ids": ["metric_a"],
                        }
                    ],
                }
            ],
        }
    )


def _item(item_id: str, text: str, priority: str) -> RecommendationItemSpec:
    return RecommendationItemSpec(
        id=item_id,
        text=text,
        priority=priority,
        source_finding_ids=["finding_1"],
        source_claim_ids=["claim_1"],
    )


def _render(items: list[RecommendationItemSpec]) -> str:
    renderer = ReportRenderer(SimpleNamespace())
    html, extras = renderer._block(
        "project",
        RecommendationBlock(type="recommendations", items=items),
        {},
        _findings(),
        {},
        {},
    )
    assert extras == []
    return html


def test_empty_priority_groups_are_omitted() -> None:
    html = _render(
        [
            _item("rec_1", "Review the leading class source.", "immediate"),
            _item("rec_2", "Compare adjacent classes next cycle.", "near_term"),
        ]
    )
    assert "立即行动" in html
    assert "近期推进" in html
    assert "持续监测" not in html
    assert html.count("class='recommendation-group'") == 2
    assert "01 立即行动" not in html
    assert "02 近期推进" not in html
    assert "<h3>立即行动</h3>" in html
    assert "<h3>近期推进</h3>" in html


def test_monitor_only_distribution_is_preserved() -> None:
    html = _render(
        [
            _item("rec_1", "Watch class A share.", "monitor"),
            _item("rec_2", "Watch class B share.", "monitor"),
            _item("rec_3", "Watch class C share.", "monitor"),
        ]
    )
    assert "立即行动" not in html
    assert "近期推进" not in html
    assert html.count("<li ") == 3
    assert "01 持续监测" not in html
    assert "<h3>持续监测</h3>" in html


def test_single_item_priority_group_has_no_list_number() -> None:
    html = _render([_item("rec_1", "Review the leading class source.", "immediate")])

    assert "<h3>立即行动</h3>" in html
    assert "<ol>" not in html
    assert "<li " not in html
    assert "<p class='recommendation-single'" in html


def test_renderer_numbers_only_multi_item_priority_group() -> None:
    html = _render(
        [
            _item("rec_1", "Confirm the spike in class A.", "immediate"),
            _item("rec_2", "Confirm the spike in class B.", "immediate"),
        ]
    )

    assert "<h3>立即行动</h3>" in html
    assert "<ol>" in html
    assert html.count("<li ") == 2


def test_renderer_does_not_rebalance_to_template() -> None:
    html = _render(
        [
            _item("rec_1", "Confirm the spike in class A.", "immediate"),
            _item("rec_2", "Confirm the spike in class B.", "immediate"),
        ]
    )
    assert html.count("<li ") == 2
    assert html.count("class='recommendation-group'") == 1
    assert "near_term" not in html
    assert "monitor" not in html


def test_recommendation_css_is_vertical_document_flow() -> None:
    css = ReportRenderer._css(REPORT_DESIGN_TOKENS["editorial"])
    start = css.index(".recommendation-groups")
    chunk = css[start : start + 400]
    assert "flex-direction:column" in chunk
    assert "grid-template-columns" not in chunk
    assert "minmax(220px,1fr)" not in chunk
