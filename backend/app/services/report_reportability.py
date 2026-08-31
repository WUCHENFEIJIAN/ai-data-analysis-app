"""Classify claims as reportable insight, limitation, or internal diagnostic."""

from __future__ import annotations

import re
from typing import Any

from app.schemas.findings import Claim, Finding, Findings, ReportRole

_PASS_CHECK = re.compile(
    r"(无缺失|不存在缺失|缺失率为?\s*0|字段无缺失|"
    r"无完全重复|未发现完全重复|未发现重复|"
    r"映射一致|未发现映射不一致|映射正常|"
    r"字段类型合法|基础(数值)?检查通过|分析脚本执行成功|"
    r"no missing|without missing|"
    r"no (complete )?duplicates?|without duplicates?|"
    r"mapping consistent|no mapping mismatch|"
    r"checks? passed|script succeeded)",
    re.IGNORECASE,
)

_LIMITATION = re.compile(
    r"(仅覆盖|时间跨度|观察窗口|观察期|"
    r"不足以|无法判断|不能据此判断|不能解释为|"
    r"季节性|长期趋势|"
    r"字段(名称)?实际|命名口径|真实层级|层级(错误|需要谨慎|判断)|"
    r"样本量(极小|过小|不足)|不适合直接评价|"
    r"缺少唯一|无法.{0,8}去重|没有.{0,8}唯一标识|"
    r"口径(限制|冲突)|分析边界|"
    r"only covers?|time (span|window|range)|cannot (infer|judge)|"
    r"seasonality|sample size too small|unique (id|identifier)|"
    r"named .{0,40} (but|actual)|field (name|named))",
    re.IGNORECASE,
)

_SEVERE = re.compile(
    r"(缺失.{0,10}\d+(\.\d+)?\s*%|大量缺失|大量重复|"
    r"严重口径冲突|时间字段异常|关键(字段|数值).{0,12}异常|"
    r"核心指标.{0,12}冲突|显著改变指标|主要结论无法信任|"
    r"missing.{0,16}\d+(\.\d+)?\s*%|cannot trust)",
    re.IGNORECASE,
)

_QUALITY_METRIC_TOKENS = (
    "missing",
    "duplicate",
    "mapping_mismatch",
    "mismatch_count",
    "invalid_date",
    "null_count",
)


def classify_claim(claim: Claim) -> ReportRole:
    text = claim.statement or ""
    if _SEVERE.search(text):
        return "report_limitation"
    if _LIMITATION.search(text):
        return "report_limitation"
    if _PASS_CHECK.search(text):
        return "internal_diagnostic"
    if claim.narrative_role == "data_quality":
        return "internal_diagnostic"
    if _quality_metrics_only(claim) and not _LIMITATION.search(text):
        return "internal_diagnostic"
    return "business_insight"


def is_severe_limitation(text: str) -> bool:
    return bool(_SEVERE.search(text or ""))


def apply_reportability(findings: Findings) -> Findings:
    updated: list[Finding] = []
    for finding in findings.findings:
        claims = [
            claim.model_copy(update={"report_role": classify_claim(claim)})
            for claim in finding.claims
        ]
        updated.append(finding.model_copy(update={"claims": claims}))
    return findings.model_copy(update={"findings": updated})


def business_findings_for_report(findings: Findings) -> list[Finding]:
    classified = apply_reportability(findings)
    business: list[Finding] = []
    for finding in classified.findings:
        claims = [claim for claim in finding.claims if claim.report_role == "business_insight"]
        if not claims:
            continue
        business.append(finding.model_copy(update={"claims": claims}))
    return business


def limitation_items(findings: Findings) -> list[dict[str, Any]]:
    classified = apply_reportability(findings)
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for finding in classified.findings:
        for claim in finding.claims:
            if claim.report_role != "report_limitation":
                continue
            key = (finding.id, claim.statement)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "claim_id": claim.claim_id,
                    "finding_id": finding.id,
                    "statement": claim.statement,
                    "narrative_role": claim.narrative_role,
                    "report_role": "report_limitation",
                    "severe": is_severe_limitation(claim.statement),
                    "evidence_metric_ids": list(claim.evidence_metric_ids),
                    "evidence_artifact_paths": list(claim.evidence_artifact_paths),
                    "related_metric_refs": list(claim.evidence_metric_ids),
                    "related_finding_ids": _related_finding_ids(
                        classified,
                        finding.id,
                        claim.evidence_artifact_paths,
                        claim.evidence_metric_ids,
                    ),
                }
            )
        has_limitation_claim = any(
            claim.report_role == "report_limitation" for claim in finding.claims
        )
        if has_limitation_claim and finding.risk.strip():
            key = (finding.id, finding.risk.strip())
            if key not in seen:
                seen.add(key)
                items.append(
                    {
                        "finding_id": finding.id,
                        "statement": finding.risk.strip(),
                        "report_role": "report_limitation",
                        "source": "finding_risk",
                        "severe": is_severe_limitation(finding.risk),
                    }
                )
    return items


def _related_finding_ids(
    findings: Findings,
    source_finding_id: str,
    artifact_paths: list[str],
    metric_ids: list[str],
) -> list[str]:
    arts = {item for item in artifact_paths if item}
    mets = {item for item in metric_ids if item}
    related: list[str] = []
    for finding in findings.findings:
        if finding.id == source_finding_id:
            continue
        finding_arts = set(finding.related_artifacts)
        finding_mets = {
            metric_id
            for claim in finding.claims
            for metric_id in claim.evidence_metric_ids
        }
        if (arts and finding_arts & arts) or (mets and finding_mets & mets):
            related.append(finding.id)
    return related


def finding_supports_recommendation(finding: Finding) -> bool:
    roles = {classify_claim(claim) for claim in finding.claims}
    return bool(roles & {"business_insight", "report_limitation"})


def _quality_metrics_only(claim: Claim) -> bool:
    ids = [item.lower() for item in claim.evidence_metric_ids]
    if not ids:
        return False
    quality_hits = 0
    for metric_id in ids:
        if metric_id in {"record_count", "row_count"}:
            continue
        if any(token in metric_id for token in _QUALITY_METRIC_TOKENS):
            quality_hits += 1
        else:
            return False
    return quality_hits > 0
