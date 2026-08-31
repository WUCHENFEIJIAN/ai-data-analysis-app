"""report_limitation must land in the affected section, not a quality chapter."""

from __future__ import annotations

from app.schemas.findings import Findings
from app.services.report_editor_prompt import ReportEditorPromptLoader
from app.services.report_editor_spec import ReportEditorSpec
from app.services.report_editorial_context import EditorialContextBuilder
from app.services.report_fallback import FallbackSpecBuilder
from app.services.report_inputs import ReportInputs
from app.services.report_limitation_attach import attach_report_limitations
from app.services.report_validator import ReportSpecValidator


def _claim(claim_id: str, statement: str, **overrides) -> dict:
    payload = {
        "claim_id": claim_id,
        "statement": statement,
        "priority": "secondary",
        "narrative_role": "context",
        "strength": 0.8,
        "evidence_metric_ids": ["metric_x"],
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
        "recommendation": overrides.pop("recommendation", "Review the leading class."),
        "related_artifacts": overrides.pop("related_artifacts", ["data/summary.json"]),
        "claims": claims,
    }
    payload.update(overrides)
    return payload


def _inputs(*items: dict) -> ReportInputs:
    return ReportInputs(
        analysis_topic="Operations review",
        title="Operations review",
        subtitle=None,
        requested_style=None,
        user_request="Analyze current operations",
        dataset_profile={"file_count": 1},
        analysis_plan={"objective": "Review operations"},
        findings=Findings.model_validate({"summary": "Analysis summary.", "findings": list(items)}),
        metrics=[],
        catalog=[],
    )


def _draft(*sections: dict, summary: str = "Core pattern holds.") -> ReportEditorSpec:
    return ReportEditorSpec.model_validate(
        {
            "headline": "category_a leads metric_x",
            "summary": summary,
            "sections": list(sections),
        }
    )


def _section(title: str, finding_id: str, data_ref: str, claim_id: str | None = None) -> dict:
    return {
        "title": title,
        "finding_refs": [finding_id],
        "claim_ids": [claim_id] if claim_id else [],
        "blocks": [
            {
                "type": "narrative",
                "text": title,
                "purpose": "Lead",
                "display_role": "lead",
                "claim_ids": [claim_id] if claim_id else [],
            },
            {
                "type": "chart",
                "data_ref": data_ref,
                "chart_type": "bar",
                "x_field": "category_a",
                "series": ["metric_x"],
                "title": title,
                "purpose": "Show the pattern",
            },
        ],
    }


def _limitation_texts(spec: ReportEditorSpec) -> list[str]:
    texts = []
    for section in spec.sections:
        for block in section.blocks:
            if getattr(block, "display_role", None) == "limitation":
                texts.append(block.text)
    return texts


def test_case_a_passing_quality_stays_out_of_body() -> None:
    inputs = _inputs(
        _finding(
            "finding_quality",
            [
                _claim(
                    "claim_no_missing",
                    "There are no missing cells and missing_rate is 0.",
                    narrative_role="data_quality",
                    evidence_metric_ids=["missing_rate", "duplicate_rate"],
                )
            ],
            title="Quality checks passed",
        ),
        _finding(
            "finding_pattern",
            [
                _claim(
                    "claim_pattern",
                    "category_a leads metric_x.",
                    narrative_role="breakdown",
                    priority="primary",
                )
            ],
            title="category_a leads metric_x",
            related_artifacts=["data/summary.json"],
        ),
    )
    context = EditorialContextBuilder.build(inputs)
    assert context["report_limitations"] == []
    spec = attach_report_limitations(
        _draft(
            _section(
                "category_a leads",
                "finding_pattern",
                "data/summary.json",
                "claim_pattern",
            )
        ),
        inputs,
    )
    assert _limitation_texts(spec) == []
    assert all("Quality checks passed" not in section.title for section in spec.sections)


def test_case_b_dimension_mismatch_enters_dimension_section() -> None:
    inputs = _inputs(
        _finding(
            "finding_dimension",
            [
                _claim(
                    "claim_dimension",
                    "category_a concentrates metric_x.",
                    narrative_role="breakdown",
                    priority="primary",
                    evidence_artifact_paths=["data/region.json"],
                )
            ],
            title="category_a concentrates metric_x",
            related_artifacts=["data/region.json"],
        ),
        _finding(
            "finding_quality",
            [
                _claim(
                    "claim_field_level",
                    "The field is named region_level_1, but actual values belong to another grain.",
                    narrative_role="risk",
                    evidence_artifact_paths=["data/region.json"],
                    evidence_metric_ids=[],
                )
            ],
            title="Field naming grain is inconsistent",
            related_artifacts=["data/region.json"],
        ),
    )
    spec = attach_report_limitations(
        _draft(
            _section(
                "Dimension comparison",
                "finding_dimension",
                "data/region.json",
                "claim_dimension",
            )
        ),
        inputs,
    )
    assert any("region_level_1" in text for text in _limitation_texts(spec))
    assert spec.sections[0].title == "Dimension comparison"
    assert all(
        "数据质量" not in section.title and "Data Quality" not in section.title
        for section in spec.sections
    )


def test_case_c_short_window_enters_trend_section() -> None:
    inputs = _inputs(
        _finding(
            "finding_trend",
            [
                _claim(
                    "claim_trend",
                    "metric_x moved upward across the observed window.",
                    narrative_role="trend",
                    priority="primary",
                    evidence_artifact_paths=["data/trend.json"],
                )
            ],
            title="metric_x moved upward",
            related_artifacts=["data/trend.json"],
        ),
        _finding(
            "finding_quality",
            [
                _claim(
                    "claim_short_window",
                    "The extract only covers two time points, so long-term trend cannot be judged.",
                    narrative_role="risk",
                    evidence_artifact_paths=["data/trend.json"],
                    evidence_metric_ids=[],
                )
            ],
            title="Window is short",
            related_artifacts=["data/trend.json"],
        ),
    )
    spec = attach_report_limitations(
        _draft(_section("Time trend", "finding_trend", "data/trend.json", "claim_trend")),
        inputs,
    )
    texts = _limitation_texts(spec)
    assert any("long-term trend cannot be judged" in text for text in texts)
    assert spec.sections[0].title == "Time trend"


def test_case_d_multi_section_limitation_enters_summary() -> None:
    statement = "A key field is missing 30%, so core conclusions cannot be trusted."
    inputs = _inputs(
        _finding(
            "finding_dimension",
            [
                _claim(
                    "claim_dimension",
                    "category_a concentrates metric_x.",
                    narrative_role="breakdown",
                    evidence_artifact_paths=["data/region.json"],
                )
            ],
            related_artifacts=["data/region.json"],
        ),
        _finding(
            "finding_trend",
            [
                _claim(
                    "claim_trend",
                    "metric_x moved upward.",
                    narrative_role="trend",
                    evidence_artifact_paths=["data/trend.json"],
                )
            ],
            related_artifacts=["data/trend.json"],
        ),
        _finding(
            "finding_quality",
            [
                _claim(
                    "claim_missing_rate",
                    statement,
                    narrative_role="data_quality",
                    evidence_artifact_paths=["data/region.json", "data/trend.json"],
                    evidence_metric_ids=[],
                )
            ],
            related_artifacts=["data/region.json", "data/trend.json"],
        ),
    )
    spec = attach_report_limitations(
        _draft(
            _section(
                "Dimension comparison",
                "finding_dimension",
                "data/region.json",
                "claim_dimension",
            ),
            _section("Time trend", "finding_trend", "data/trend.json", "claim_trend"),
            summary="category_a leads metric_x.",
        ),
        inputs,
    )
    assert statement in spec.summary
    assert len(_limitation_texts(spec)) == 2


def test_case_e_passing_checks_cannot_form_independent_section() -> None:
    inputs = _inputs(
        _finding(
            "finding_pattern",
            [
                _claim(
                    "claim_pattern",
                    "category_a leads metric_x.",
                    narrative_role="breakdown",
                    priority="primary",
                )
            ],
            title="category_a leads metric_x",
        ),
        _finding(
            "finding_quality",
            [
                _claim(
                    "claim_no_missing",
                    "There are no missing cells and no complete duplicate rows.",
                    narrative_role="data_quality",
                    evidence_metric_ids=["missing_rate", "duplicate_rate"],
                )
            ],
            title="Quality checks passed",
        ),
    )
    draft = FallbackSpecBuilder().build_editor_spec(inputs)
    titles = [section.title for section in draft.sections]
    assert "Quality checks passed" not in titles
    roles = [
        block.display_role
        for section in draft.sections
        for block in section.blocks
        if hasattr(block, "display_role")
    ]
    assert "limitation" not in roles


def test_validator_attaches_limitation_without_quality_chapter() -> None:
    inputs = _inputs(
        _finding(
            "finding_dimension",
            [
                _claim(
                    "claim_dimension",
                    "category_a concentrates metric_x.",
                    narrative_role="breakdown",
                    priority="primary",
                    evidence_artifact_paths=["data/region.json"],
                )
            ],
            related_artifacts=["data/region.json"],
        ),
        _finding(
            "finding_quality",
            [
                _claim(
                    "claim_field_level",
                    "The field is named region_level_1, but actual values belong to another grain.",
                    narrative_role="risk",
                    evidence_artifact_paths=["data/region.json"],
                    evidence_metric_ids=[],
                )
            ],
            related_artifacts=["data/region.json"],
        ),
    )
    draft = _draft(
        {
            "title": "Dimension comparison",
            "finding_refs": ["finding_dimension"],
            "claim_ids": ["claim_dimension"],
            "blocks": [
                {
                    "type": "narrative",
                    "text": "category_a concentrates metric_x.",
                    "purpose": "Lead",
                    "display_role": "lead",
                    "claim_ids": ["claim_dimension"],
                }
            ],
        }
    )
    result = ReportSpecValidator.validate(draft, inputs)
    texts = _limitation_texts(result.spec)
    assert any("region_level_1" in text for text in texts)
    assert all("数据质量" not in section.title for section in result.spec.sections)


def test_prompt_keeps_limitations_out_of_quality_chapter() -> None:
    prompt = ReportEditorPromptLoader().load()
    assert "不要恢复独立的 Data Quality Section" in prompt
    assert (
        "internal_diagnostic` 不得进入正文" in prompt
        or "internal_diagnostic 不得进入正文" in prompt
    )
    assert "字段命名与实际取值层级存在不一致" in prompt
