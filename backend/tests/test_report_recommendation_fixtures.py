"""Recommendation count and priority follow action identity, not a template."""

from __future__ import annotations

from pathlib import Path

from app.schemas.findings import Findings
from app.services.report_editor_assembler import ReportEditorAssembler
from app.services.report_editor_spec import ReportEditorSpec
from app.services.report_inputs import ReportInputs


def _finding(finding_id: str, statement: str) -> dict:
    return {
        "id": finding_id,
        "title": statement,
        "evidence": [statement],
        "risk": "Current coverage is limited.",
        "recommendation": "Review the measured class.",
        "claims": [
            {
                "claim_id": finding_id.replace("finding", "claim"),
                "statement": statement,
                "priority": "primary",
                "narrative_role": "driver",
                "evidence_metric_ids": ["metric_a"],
            }
        ],
    }


def _inputs(findings: list[dict]) -> ReportInputs:
    return ReportInputs(
        analysis_topic="Operations review",
        title="Operations review",
        subtitle=None,
        requested_style=None,
        user_request="Analyze current operations",
        dataset_profile={},
        analysis_plan={},
        findings=Findings.model_validate(
            {"summary": "Several independent drivers.", "findings": findings}
        ),
        metrics=[],
        catalog=[],
    )


def _draft(items: list[dict], finding_refs: list[str]) -> ReportEditorSpec:
    return ReportEditorSpec.model_validate(
        {
            "headline": "Independent drivers require independent actions",
            "summary": "Action count follows distinct action identities.",
            "sections": [
                {
                    "title": "Next actions",
                    "finding_refs": finding_refs,
                    "blocks": [
                        {
                            "type": "narrative",
                            "text": "Distinct drivers remain separate actions.",
                            "claim_ids": [
                                item.replace("finding", "claim") for item in finding_refs
                            ],
                        },
                        (
                            {"type": "recommendations", "items": items}
                            if items
                            else {
                                "type": "narrative",
                                "text": "No sufficiently actionable next step is supported.",
                            }
                        ),
                    ],
                }
            ],
        }
    )


def _priorities(spec) -> list[str]:
    rec = next(
        block
        for section in spec.sections
        for block in section.blocks
        if block.type == "recommendations"
    )
    return [item.priority for item in rec.items]


def test_fixture_e_four_distinct_actions_keep_four_recommendations() -> None:
    findings = [
        _finding("finding_a", "Class A grew faster than total volume."),
        _finding("finding_b", "Class B concentrates most of the volume."),
        _finding("finding_c", "Team C has higher output per person."),
        _finding("finding_d", "Product D dominates the mix."),
    ]
    items = [
        {
            "text": "Review the source of class A growth.",
            "priority": "immediate",
            "source_finding_ids": ["finding_a"],
            "source_claim_ids": ["claim_a"],
        },
        {
            "text": "Test whether class B methods copy to other classes.",
            "priority": "immediate",
            "source_finding_ids": ["finding_b"],
            "source_claim_ids": ["claim_b"],
        },
        {
            "text": "Compare team C coaching against other teams.",
            "priority": "near_term",
            "source_finding_ids": ["finding_c"],
            "source_claim_ids": ["claim_c"],
        },
        {
            "text": "Compare product D with substitutes.",
            "priority": "near_term",
            "source_finding_ids": ["finding_d"],
            "source_claim_ids": ["claim_d"],
        },
    ]
    spec = ReportEditorAssembler().assemble(
        _draft(items, ["finding_a", "finding_b", "finding_c", "finding_d"]),
        _inputs(findings),
    )
    assert _priorities(spec) == ["immediate", "immediate", "near_term", "near_term"]


def test_fixture_f_same_action_identity_keeps_multiple_finding_refs() -> None:
    findings = [
        _finding("finding_a", "Region A concentrates volume."),
        _finding("finding_b", "City B inside region A concentrates volume."),
        _finding("finding_c", "Team C inside region A concentrates volume."),
    ]
    items = [
        {
            "text": "Replay the core-region method and test copyability.",
            "priority": "near_term",
            "source_finding_ids": ["finding_a", "finding_b", "finding_c"],
            "source_claim_ids": ["claim_a", "claim_b", "claim_c"],
        }
    ]
    spec = ReportEditorAssembler().assemble(
        _draft(items, ["finding_a", "finding_b", "finding_c"]),
        _inputs(findings),
    )
    rec = next(
        block
        for section in spec.sections
        for block in section.blocks
        if block.type == "recommendations"
    )
    assert len(rec.items) == 1
    assert rec.items[0].source_finding_ids == ["finding_a", "finding_b", "finding_c"]


def test_fixture_g_two_identities_keep_two_recommendations() -> None:
    findings = [
        _finding("finding_a", "Region A concentrates volume."),
        _finding("finding_b", "City B concentrates volume."),
        _finding("finding_c", "People coaching quality differs."),
    ]
    items = [
        {
            "text": "Replay the core-region method.",
            "priority": "near_term",
            "source_finding_ids": ["finding_a", "finding_b"],
            "source_claim_ids": ["claim_a", "claim_b"],
        },
        {
            "text": "Coach the lower-output people separately.",
            "priority": "near_term",
            "source_finding_ids": ["finding_c"],
            "source_claim_ids": ["claim_c"],
        },
    ]
    spec = ReportEditorAssembler().assemble(
        _draft(items, ["finding_a", "finding_b", "finding_c"]),
        _inputs(findings),
    )
    rec = next(
        block
        for section in spec.sections
        for block in section.blocks
        if block.type == "recommendations"
    )
    assert len(rec.items) == 2


def test_fixture_h_all_monitor_is_allowed() -> None:
    findings = [_finding("finding_a", "Share movement is still small.")]
    items = [
        {
            "text": "Watch share A.",
            "priority": "monitor",
            "source_finding_ids": ["finding_a"],
            "source_claim_ids": ["claim_a"],
        },
        {
            "text": "Watch share B.",
            "priority": "monitor",
            "source_finding_ids": ["finding_a"],
            "source_claim_ids": ["claim_a"],
        },
        {
            "text": "Watch share C.",
            "priority": "monitor",
            "source_finding_ids": ["finding_a"],
            "source_claim_ids": ["claim_a"],
        },
    ]
    spec = ReportEditorAssembler().assemble(_draft(items, ["finding_a"]), _inputs(findings))
    assert _priorities(spec) == ["monitor", "monitor", "monitor"]


def test_fixture_i_zero_recommendations_are_allowed() -> None:
    findings = [_finding("finding_a", "The pattern is descriptive only.")]
    spec = ReportEditorAssembler().assemble(_draft([], ["finding_a"]), _inputs(findings))
    recs = [
        block
        for section in spec.sections
        for block in section.blocks
        if block.type == "recommendations"
    ]
    assert recs == []


def test_cross_dataset_quality_pass_does_not_enter_editor() -> None:
    from app.services.report_editorial_context import EditorialContextBuilder

    datasets = [
        ("service-operations", "There are no missing cells in ticket fields."),
        ("inventory-operations", "No complete duplicate stock rows were found."),
        ("neutral-fields", "Mapping is consistent across class fields."),
    ]
    for topic, statement in datasets:
        findings = Findings.model_validate(
            {
                "summary": topic,
                "findings": [
                    {
                        "id": "finding_quality",
                        "title": "Quality checks passed",
                        "evidence": [statement],
                        "risk": "No material quality defect was found.",
                        "recommendation": "Keep monitoring data quality.",
                        "claims": [
                            {
                                "claim_id": "claim_quality",
                                "statement": statement,
                                "narrative_role": "data_quality",
                                "evidence_metric_ids": ["missing_cell_count"],
                            }
                        ],
                    },
                    _finding("finding_a", f"{topic} has a verified class difference."),
                ],
            }
        )
        inputs = ReportInputs(
            analysis_topic=topic,
            title=topic,
            subtitle=None,
            requested_style=None,
            user_request=f"Analyze {topic}",
            dataset_profile={},
            analysis_plan={},
            findings=findings,
            metrics=[],
            catalog=[],
        )
        context = EditorialContextBuilder.build(inputs)
        assert [item["id"] for item in context["findings"]] == ["finding_a"]
        assert all(item["claim_id"] != "claim_quality" for item in context["claims"])


def test_prompt_guards_against_unsupported_recommendation_precision() -> None:
    prompt = (Path(__file__).resolve().parents[2] / "REPORT_EDITOR_SYSTEM_PROMPT.md").read_text(
        encoding="utf-8"
    )
    assert "不得创造当前 Evidence、用户明确要求或已声明业务规则中不存在的精确参数" in prompt
    assert "精确时间窗口" in prompt
    assert "根据历史分布设定阈值" in prompt
    assert "精确参数已经出现在 Evidence、用户要求或明确业务规则中" in prompt
