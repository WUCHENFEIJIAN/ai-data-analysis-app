
"""Generic editorial lint fixtures. No business-data rules."""

from __future__ import annotations

from app.services.report_editor_spec import ReportEditorSpec
from app.services.report_editorial_lint import (
    CONSECUTIVE_NARRATIVE_SAME_ROLE,
    EXACT_PARAGRAPH_DUPLICATE,
    INTERPRETATION_WITHOUT_VISUAL,
    NARRATIVE_CLAIM_OVERLAP,
    NARRATIVE_METRIC_OVERLAP,
    EditorialLint,
)


def _spec(sections: list[dict], summary: str = "Theme X happened.") -> dict:
    return {
        "headline": "Theme X is the primary judgment",
        "summary": summary,
        "kpis": [],
        "sections": sections,
    }


def _section(title: str, blocks: list[dict], lead: str | None = None) -> dict:
    payload = {"title": title, "blocks": blocks, "layout": "flow"}
    if lead is not None:
        payload["lead"] = lead
    return payload


def test_exact_duplicate_lead_and_supporting() -> None:
    spec = ReportEditorSpec.model_validate(
        _spec(
            [
                _section(
                    "Scale",
                    lead="Metric X grew rapidly.",
                    blocks=[
                        {
                            "type": "narrative",
                            "text": "Metric X grew rapidly.",
                            "display_role": "supporting_narrative",
                        }
                    ],
                )
            ]
        )
    )
    result = EditorialLint.check(spec)
    assert EXACT_PARAGRAPH_DUPLICATE in result.codes()
    assert result.should_revise() is False


def test_same_claim_different_function_is_allowed() -> None:
    spec = ReportEditorSpec.model_validate(
        _spec(
            [
                _section(
                    "Scale",
                    blocks=[
                        {
                            "type": "narrative",
                            "text": "Metric X grew rapidly.",
                            "display_role": "lead",
                            "claim_ids": ["claim_x"],
                            "metric_refs": ["metric_x"],
                        },
                        {
                            "type": "chart",
                            "data_ref": "data/summary.csv",
                            "chart_type": "bar",
                            "x_field": "name",
                            "series": ["metric_x"],
                            "title": "Metric X",
                            "purpose": "Show growth",
                        },
                        {
                            "type": "narrative",
                            "text": "Metric X grew faster than Metric Y, indicating mix shift.",
                            "display_role": "evidence_interpretation",
                            "related_block_id": "data/summary.csv",
                            "claim_ids": ["claim_x"],
                            "metric_refs": ["metric_x", "metric_y"],
                        },
                    ],
                )
            ]
        )
    )
    result = EditorialLint.check(spec)
    assert EXACT_PARAGRAPH_DUPLICATE not in result.codes()
    assert INTERPRETATION_WITHOUT_VISUAL not in result.codes()
    assert result.should_revise() is False


def test_identical_claim_and_metric_sets_warn_and_revise() -> None:
    claims = ["claim_a", "claim_b", "claim_c"]
    metrics = ["metric_x", "metric_y", "metric_z"]
    spec = ReportEditorSpec.model_validate(
        _spec(
            [
                _section(
                    "Scale",
                    blocks=[
                        {
                            "type": "narrative",
                            "text": "The section restates A, B and C.",
                            "display_role": "lead",
                            "claim_ids": claims,
                            "metric_refs": metrics,
                        },
                        {
                            "type": "chart",
                            "data_ref": "data/summary.csv",
                            "chart_type": "bar",
                            "x_field": "name",
                            "series": ["metric_x"],
                            "title": "Metrics",
                            "purpose": "Show evidence",
                        },
                        {
                            "type": "narrative",
                            "text": "The interpretation restates A, B and C again.",
                            "display_role": "evidence_interpretation",
                            "related_block_id": "data/summary.csv",
                            "claim_ids": claims,
                            "metric_refs": metrics,
                        },
                    ],
                )
            ]
        )
    )
    result = EditorialLint.check(spec)
    assert NARRATIVE_CLAIM_OVERLAP in result.codes()
    assert NARRATIVE_METRIC_OVERLAP in result.codes()
    assert result.should_revise() is True


def test_interpretation_without_visual() -> None:
    spec = ReportEditorSpec.model_validate(
        _spec(
            [
                _section(
                    "Scale",
                    blocks=[
                        {
                            "type": "narrative",
                            "text": "Readers should see a relationship here.",
                            "display_role": "evidence_interpretation",
                            "related_block_id": None,
                        }
                    ],
                )
            ]
        )
    )
    result = EditorialLint.check(spec)
    assert INTERPRETATION_WITHOUT_VISUAL in result.codes()


def test_summary_and_body_theme_overlap_is_allowed() -> None:
    spec = ReportEditorSpec.model_validate(
        _spec(
            [
                _section(
                    "Scale",
                    lead="Scale expanded mainly through customer count.",
                    blocks=[
                        {
                            "type": "narrative",
                            "text": (
                                "Customer growth outpaced amount growth."
                            ),
                            "display_role": "evidence_interpretation",
                            "related_block_id": "data/summary.csv",
                        },
                        {
                            "type": "chart",
                            "data_ref": "data/summary.csv",
                            "chart_type": "bar",
                            "x_field": "name",
                            "series": ["metric_x"],
                            "title": "Growth",
                            "purpose": "Show the relationship",
                        },
                    ],
                )
            ],
            summary="Scale expanded mainly through customer count, with more detail in the body.",
        )
    )
    result = EditorialLint.check(spec)
    assert EXACT_PARAGRAPH_DUPLICATE not in result.codes()
    assert result.should_revise() is False


def test_callout_with_new_information_is_allowed() -> None:
    spec = ReportEditorSpec.model_validate(
        _spec(
            [
                _section(
                    "Scale",
                    lead="Scale expanded mainly through customer count.",
                    blocks=[
                        {
                            "type": "chart",
                            "data_ref": "data/summary.csv",
                            "chart_type": "bar",
                            "x_field": "name",
                            "series": ["metric_x"],
                            "title": "Growth",
                            "purpose": "Show evidence",
                        },
                        {
                            "type": "callout",
                            "tone": "note",
                            "title": "Data limit",
                            "text": (
                                "The source has no customer id."
                            ),
                        },
                    ],
                )
            ]
        )
    )
    result = EditorialLint.check(spec)
    assert EXACT_PARAGRAPH_DUPLICATE not in result.codes()


def test_callout_repeating_lead_is_a_warning() -> None:
    spec = ReportEditorSpec.model_validate(
        _spec(
            [
                _section(
                    "Scale",
                    lead="Scale expanded mainly through customer count.",
                    blocks=[
                        {
                            "type": "callout",
                            "tone": "insight",
                            "title": "Repeat",
                            "text": "Scale expanded mainly through customer count.",
                        }
                    ],
                )
            ]
        )
    )
    result = EditorialLint.check(spec)
    assert EXACT_PARAGRAPH_DUPLICATE in result.codes()
    assert result.should_revise() is True


def test_consecutive_same_role_is_a_warning() -> None:
    spec = ReportEditorSpec.model_validate(
        _spec(
            [
                _section(
                    "Scale",
                    blocks=[
                        {
                            "type": "narrative",
                            "text": "First supporting point.",
                            "display_role": "supporting_narrative",
                        },
                        {
                            "type": "narrative",
                            "text": "Second supporting point.",
                            "display_role": "supporting_narrative",
                        },
                    ],
                )
            ]
        )
    )
    result = EditorialLint.check(spec)
    assert CONSECUTIVE_NARRATIVE_SAME_ROLE in result.codes()
    assert result.should_revise() is True
