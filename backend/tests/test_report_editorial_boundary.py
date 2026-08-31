"""Editorial context keeps internal diagnostics out of the Report Editor."""

from __future__ import annotations

from app.schemas.findings import Findings
from app.services.report_editor_spec import ReportEditorSpec
from app.services.report_editorial_context import EditorialContextBuilder
from app.services.report_fallback import FallbackSpecBuilder
from app.services.report_inputs import ReportInputs
from app.services.report_validator import ReportSpecValidator


def _claim(claim_id: str, statement: str, **overrides) -> dict:
    payload = {
        "claim_id": claim_id,
        "statement": statement,
        "priority": "secondary",
        "narrative_role": "context",
        "strength": 0.8,
        "evidence_metric_ids": ["metric_a"],
        "evidence_artifact_paths": ["data/summary.json"],
    }
    payload.update(overrides)
    return payload


def _finding(finding_id: str, claims: list[dict], **overrides) -> dict:
    payload = {
        "id": finding_id,
        "title": overrides.pop("title", "Observed pattern"),
        "evidence": overrides.pop("evidence", ["Computed from analysis artifacts."]),
        "risk": overrides.pop("risk", "Interpretation depends on current coverage."),
        "recommendation": overrides.pop(
            "recommendation", "Review the leading class."
        ),
        "related_artifacts": ["data/summary.json"],
        "claims": claims,
    }
    payload.update(overrides)
    return payload


def _findings(*items: dict, summary: str = "Analysis summary.") -> Findings:
    return Findings.model_validate({"summary": summary, "findings": list(items)})


def _inputs(findings: Findings) -> ReportInputs:
    return ReportInputs(
        analysis_topic="Operations review",
        title="Operations review",
        subtitle=None,
        requested_style=None,
        user_request="Analyze current operations",
        dataset_profile={"file_count": 1},
        analysis_plan={"objective": "Review operations"},
        findings=findings,
        metrics=[],
        catalog=[],
    )


def _quality_pass_finding() -> dict:
    return _finding(
        "finding_quality",
        [
            _claim(
                "claim_no_missing",
                "There are no missing cells and no complete duplicate rows.",
                narrative_role="data_quality",
                evidence_metric_ids=["missing_cell_count", "exact_duplicate_row_count"],
            ),
            _claim(
                "claim_mapping_ok",
                "Mapping is consistent across product and group fields.",
                narrative_role="data_quality",
                evidence_metric_ids=["product_mapping_mismatch_count"],
            ),
        ],
        title="Quality checks passed",
        evidence=["No missing cells.", "No complete duplicates."],
        risk="No material quality defect was found.",
        recommendation="Keep monitoring data quality.",
    )


def _business_finding() -> dict:
    return _finding(
        "finding_growth",
        [
            _claim(
                "claim_growth",
                "The leading class grew faster than total volume.",
                narrative_role="change",
                priority="primary",
            )
        ],
        title="Leading class grew faster than total volume",
    )


def test_fixture_a_pass_checks_leave_editorial_context() -> None:
    context = EditorialContextBuilder.build(_inputs(_findings(_quality_pass_finding())))
    assert context["findings"] == []
    assert context["claims"] == []
    assert not any(
        "no missing" in item["statement"].lower()
        for item in context["report_limitations"]
    )
    assert context["constraints"]["internal_diagnostics_excluded"] is True


def test_fixture_b_short_window_is_limitation_not_section_candidate() -> None:
    findings = _findings(
        _business_finding(),
        _finding(
            "finding_quality",
            [
                _claim(
                    "claim_no_missing",
                    "There are no missing cells.",
                    narrative_role="data_quality",
                    evidence_metric_ids=["missing_cell_count"],
                ),
                _claim(
                    "claim_short_window",
                    "The extract only covers two months, so long-term trend cannot be judged.",
                    narrative_role="risk",
                ),
            ],
            title="Quality checks passed but the window is short",
        ),
    )
    context = EditorialContextBuilder.build(_inputs(findings))
    finding_ids = [item["id"] for item in context["findings"]]
    claim_ids = [item["claim_id"] for item in context["claims"]]
    limitation_ids = [
        item.get("claim_id") for item in context["report_limitations"] if item.get("claim_id")
    ]
    assert finding_ids == ["finding_growth"]
    assert "claim_no_missing" not in claim_ids
    assert "claim_short_window" in limitation_ids
    assert "claim_short_window" not in claim_ids


def test_fixture_c_field_ambiguity_enters_limitations() -> None:
    findings = _findings(
        _finding(
            "finding_quality",
            [
                _claim(
                    "claim_field_level",
                    "The field is named Province, but actual values are cities.",
                    narrative_role="risk",
                )
            ],
            title="Geographic field names do not match values",
            risk="The region field name does not match the actual geographic level.",
        )
    )
    context = EditorialContextBuilder.build(_inputs(findings))
    assert context["findings"] == []
    statements = [item["statement"] for item in context["report_limitations"]]
    assert any("Province" in item or "geographic" in item for item in statements)


def test_fixture_d_severe_quality_issue_is_limitation() -> None:
    findings = _findings(
        _finding(
            "finding_quality",
            [
                _claim(
                    "claim_missing_rate",
                    "A key field is missing 30%, so core conclusions cannot be trusted.",
                    narrative_role="data_quality",
                    evidence_metric_ids=["missing_cell_count"],
                )
            ],
            title="Key field missingness is material",
        )
    )
    context = EditorialContextBuilder.build(_inputs(findings))
    assert context["findings"] == []
    severe = [item for item in context["report_limitations"] if item.get("severe")]
    assert severe
    assert severe[0].get("claim_id") == "claim_missing_rate"


def test_fallback_skips_internal_quality_finding() -> None:
    inputs = _inputs(_findings(_business_finding(), _quality_pass_finding()))
    draft = FallbackSpecBuilder().build_editor_spec(inputs)
    titles = [section.title for section in draft.sections]
    assert "Quality checks passed" not in titles
    assert any("grew faster" in title for title in titles)


def test_validator_drops_internal_diagnostic_recommendation() -> None:
    inputs = _inputs(_findings(_quality_pass_finding(), _business_finding()))
    draft = ReportEditorSpec.model_validate(
        {
            "headline": "Leading class grew faster than total volume",
            "summary": "Growth is concentrated in one class.",
            "sections": [
                {
                    "title": "Keep monitoring quality",
                    "finding_refs": ["finding_quality"],
                    "blocks": [
                        {
                            "type": "recommendations",
                            "items": [
                                {
                                    "text": "Keep monitoring data quality.",
                                    "priority": "monitor",
                                    "source_finding_ids": ["finding_quality"],
                                    "source_claim_ids": ["claim_no_missing"],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )
    result = ReportSpecValidator.validate(draft, inputs)
    assert any(issue.code == "recommendation.internal_diagnostic" for issue in result.issues)
    rec_blocks = [
        block
        for section in result.spec.sections
        for block in section.blocks
        if block.type == "recommendations"
    ]
    assert rec_blocks == []


def test_validator_keeps_limitation_governance_recommendation() -> None:
    inputs = _inputs(
        _findings(
            _finding(
                "finding_quality",
                [
                    _claim(
                        "claim_field_level",
                        "The field is named Province, but actual values are cities.",
                        narrative_role="risk",
                    )
                ],
                title="Geographic field names do not match values",
            )
        )
    )
    draft = ReportEditorSpec.model_validate(
        {
            "headline": "Geographic labels need a consistent grain",
            "summary": "Region labels do not match the stored grain.",
            "sections": [
                {
                    "title": "Unify the geographic field grain",
                    "finding_refs": ["finding_quality"],
                    "claim_ids": ["claim_field_level"],
                    "blocks": [
                        {
                            "type": "recommendations",
                            "items": [
                                {
                                    "text": "Unify the geographic field grain before scoring.",
                                    "priority": "near_term",
                                    "source_finding_ids": ["finding_quality"],
                                    "source_claim_ids": ["claim_field_level"],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )
    result = ReportSpecValidator.validate(draft, inputs)
    rec = next(
        block
        for section in result.spec.sections
        for block in section.blocks
        if block.type == "recommendations"
    )
    assert len(rec.items) == 1
    assert rec.items[0].priority == "near_term"


def test_prompt_documents_reportability_and_recommendations() -> None:
    from app.services.report_editor_prompt import ReportEditorPromptLoader

    prompt = ReportEditorPromptLoader().load()
    assert "质量检查通过永远不能独立成章" in prompt
    assert "report_limitations" in prompt
    assert "Finding ≠ Recommendation" in prompt
    assert "Action Identity" in prompt
    assert "固定 1 immediate / 2 near_term / 1 monitor" in prompt
    assert "0条 Recommendation" in prompt
    assert "internal_diagnostic` 不得产生 Recommendation" in prompt or (
        "internal_diagnostic 不得产生 Recommendation" in prompt
    )
    assert "不要为了填满 immediate / near_term / monitor" in prompt
