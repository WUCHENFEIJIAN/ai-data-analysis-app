"""Claim-level reportability classification. Domain-agnostic fixtures only."""

from __future__ import annotations

from pathlib import Path

from app.schemas.findings import Claim, Finding, Findings
from app.services.report_reportability import (
    apply_reportability,
    business_findings_for_report,
    classify_claim,
    is_severe_limitation,
    limitation_items,
)


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


def _finding(finding_id: str, claims: list[dict], **overrides) -> Finding:
    payload = {
        "id": finding_id,
        "title": overrides.pop("title", "Observed pattern"),
        "evidence": overrides.pop("evidence", ["Computed from analysis artifacts."]),
        "risk": overrides.pop("risk", "Interpretation depends on current coverage."),
        "recommendation": overrides.pop("recommendation", "Review the leading class."),
        "related_artifacts": ["data/summary.json"],
        "claims": claims,
    }
    payload.update(overrides)
    return Finding.model_validate(payload)


def test_pass_quality_checks_are_internal_diagnostics() -> None:
    claims = [
        Claim.model_validate(
            _claim(
                "claim_no_missing",
                "There are no missing cells and no complete duplicate rows.",
                narrative_role="data_quality",
                evidence_metric_ids=["missing_cell_count", "exact_duplicate_row_count"],
            )
        ),
        Claim.model_validate(
            _claim(
                "claim_mapping_ok",
                "产品层级和业务组层级均未发现映射不一致记录。",
                narrative_role="data_quality",
                evidence_metric_ids=["product_mapping_mismatch_count"],
            )
        ),
        Claim.model_validate(
            _claim(
                "claim_types_ok",
                "字段类型合法，基础数值检查通过。",
                narrative_role="data_quality",
            )
        ),
    ]
    roles = [classify_claim(claim) for claim in claims]
    assert roles == ["internal_diagnostic"] * 3


def test_short_time_window_is_limitation_not_quality_section() -> None:
    claim = Claim.model_validate(
        _claim(
            "claim_short_window",
            "数据仅覆盖两个月，时间跨度为61个自然日。",
            narrative_role="risk",
        )
    )
    assert classify_claim(claim) == "report_limitation"
    assert not is_severe_limitation(claim.statement)


def test_field_level_ambiguity_is_limitation() -> None:
    claim = Claim.model_validate(
        _claim(
            "claim_field_level",
            "字段名称叫 Province，但实际值是城市，命名口径可能误导层级判断。",
            narrative_role="risk",
        )
    )
    assert classify_claim(claim) == "report_limitation"


def test_severe_missingness_is_limitation() -> None:
    claim = Claim.model_validate(
        _claim(
            "claim_missing_rate",
            "关键字段缺失30%，核心结论可信度下降。",
            narrative_role="data_quality",
            evidence_metric_ids=["missing_cell_count"],
        )
    )
    assert classify_claim(claim) == "report_limitation"
    assert is_severe_limitation(claim.statement)


def test_business_claims_stay_business_insight() -> None:
    claim = Claim.model_validate(
        _claim(
            "claim_growth",
            "The leading class grew faster than total volume.",
            narrative_role="change",
            priority="primary",
        )
    )
    assert classify_claim(claim) == "business_insight"


def test_mixed_quality_finding_is_split_not_dropped() -> None:
    findings = apply_reportability(
        Findings.model_validate(
            {
                "summary": "Quality mixed with a coverage limit.",
                "findings": [
                    _finding(
                        "finding_quality",
                        [
                            _claim(
                                "claim_no_missing",
                                "1657条记录不存在缺失单元格和完全重复记录。",
                                narrative_role="data_quality",
                                evidence_metric_ids=[
                                    "record_count",
                                    "missing_cell_count",
                                    "exact_duplicate_row_count",
                                ],
                            ),
                            _claim(
                                "claim_mapping_ok",
                                "未发现映射不一致记录。",
                                narrative_role="data_quality",
                                evidence_metric_ids=["product_mapping_mismatch_count"],
                            ),
                            _claim(
                                "claim_short_window",
                                "数据仅覆盖2020年4月1日至5月31日，时间跨度为61个自然日。",
                                narrative_role="risk",
                            ),
                        ],
                        title="Quality checks passed but coverage is short",
                        risk="The region field name does not match the actual geographic level.",
                    ).model_dump(by_alias=True)
                ],
            }
        )
    )
    roles = {claim.claim_id: claim.report_role for claim in findings.findings[0].claims}
    assert roles["claim_no_missing"] == "internal_diagnostic"
    assert roles["claim_mapping_ok"] == "internal_diagnostic"
    assert roles["claim_short_window"] == "report_limitation"
    assert business_findings_for_report(findings) == []
    limitation_statements = [item["statement"] for item in limitation_items(findings)]
    assert any("仅覆盖" in item for item in limitation_statements)
    assert any("geographic level" in item for item in limitation_statements)


def test_classifier_has_no_sales_business_predicates() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        .joinpath("app/services/report_reportability.py")
        .read_text(encoding="utf-8")
    )
    for token in ("借呗", "华东", "销售工号", "杭州二组", "成交金额"):
        assert token not in source
