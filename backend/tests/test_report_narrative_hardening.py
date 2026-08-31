"""Generic narrative role hardening tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.report_editor_assembler import ReportEditorAssembler
from app.services.report_editor_prompt import ReportEditorPromptLoader
from app.services.report_editor_spec import ReportEditorNarrativeBlock, ReportEditorSpec
from app.services.report_spec import NarrativeBlock, RecommendationBlock
from tests.test_report_metric_fidelity import _inputs


def _spec(blocks: list[dict], lead: str | None = None) -> ReportEditorSpec:
    section = {"title": "Narrative roles", "blocks": blocks}
    if lead is not None:
        section["lead"] = lead
    return ReportEditorSpec.model_validate(
        {
            "headline": "The report has one clear lead",
            "summary": "The report separates judgments, limits, and actions.",
            "sections": [section],
        }
    )


def _narrative(text: str, role: str = "supporting_narrative") -> dict:
    return {"type": "narrative", "text": text, "display_role": role}


def test_one_section_has_at_most_one_lead() -> None:
    spec = _spec(
        [_narrative("Block lead", "lead"), _narrative("Supporting context")],
        lead="Section lead",
    )
    assembled = ReportEditorAssembler().assemble(spec, _inputs())
    leads = [
        block
        for block in assembled.sections[0].blocks
        if isinstance(block, NarrativeBlock) and block.display_role == "lead"
    ]
    assert len(leads) == 1
    assert leads[0].text == "Section lead"


def test_multiple_leads_keep_first_and_demote_or_omit_followers() -> None:
    spec = _spec(
        [
            _narrative("First lead", "lead"),
            _narrative("Second distinct lead", "lead"),
            _narrative("First lead", "lead"),
        ]
    )
    assembled = ReportEditorAssembler().assemble(spec, _inputs())
    narratives = [
        block for block in assembled.sections[0].blocks if isinstance(block, NarrativeBlock)
    ]
    assert [block.display_role for block in narratives] == [
        "lead",
        "supporting_narrative",
    ]
    assert [block.text for block in narratives] == ["First lead", "Second distinct lead"]


def test_limitation_only_text_is_a_valid_narrative_role() -> None:
    block = ReportEditorNarrativeBlock.model_validate(
        {
            "type": "narrative",
            "text": "The extract covers only a short observation window.",
            "display_role": "limitation",
        }
    )
    assert block.display_role == "limitation"


def test_limitation_cannot_carry_recommendation_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ReportEditorNarrativeBlock.model_validate(
            {
                "type": "narrative",
                "text": "The observation window is short.",
                "display_role": "limitation",
                "priority": "near_term",
                "action_target": "category_a",
            }
        )


def test_recommendation_survives_limitation_in_same_section() -> None:
    spec = _spec(
        [
            _narrative("The observation window is short.", "limitation"),
            {
                "type": "recommendations",
                "items": [
                    {
                        "text": "Review the next observation window.",
                        "priority": "near_term",
                        "source_finding_ids": ["finding_visual"],
                    }
                ],
            },
        ]
    )
    assembled = ReportEditorAssembler().assemble(spec, _inputs())
    assert not any(
        isinstance(block, RecommendationBlock) for block in assembled.sections[0].blocks
    )
    assert any(
        isinstance(block, RecommendationBlock) for block in assembled.sections[-1].blocks
    )
    assert assembled.sections[-1].title == "行动建议"


def test_prompt_hardens_narrative_roles() -> None:
    prompt = ReportEditorPromptLoader().load()
    assert "一个 Section 只生成一个 Lead" in prompt
    assert "后续非重复内容降为 supporting_narrative" in prompt
    assert "limitation 文本不得包含" in prompt
    assert "这些内容只能进入 Recommendation" in prompt
    assert "priority、action_target" in prompt