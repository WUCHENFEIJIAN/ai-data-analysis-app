"""Deterministic guard for unsupported exact recommendation parameters."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.schemas.findings import Finding, Findings
from app.services.metric_contract import MetricDefinition

_NUMBER = r"\d+(?:[,.]\d+)?"
_WINDOW_SEQUENCE = re.compile(
    rf"(?P<values>{_NUMBER}(?:\s*/\s*{_NUMBER}){{1,2}})\s*(?:日|天|day|days|周|week|weeks|月|month|months)",
    re.IGNORECASE,
)
_WINDOW_SINGLE = re.compile(
    rf"(?P<value>{_NUMBER})\s*(?:日|天|day|days|周|week|weeks|月|month|months)",
    re.IGNORECASE,
)
_PERCENT = re.compile(rf"(?P<value>{_NUMBER})\s*%")
_RANKING_OR_LIMIT = re.compile(
    rf"(?:top|前|至少|最多|目标|target|at\s+least|at\s+most)\s*(?P<value>{_NUMBER})",
    re.IGNORECASE,
)
_CHANGE_TARGET = re.compile(
    rf"(?:提升|增长|下降|增加|减少|达到|超过|低于|不高于|不低于|increase(?:\s+to)?|grow(?:\s+to)?|decrease(?:\s+to)?|below|above)\s*(?:到|至|to)?\s*(?P<value>{_NUMBER})\s*(?P<unit>%|个|次|名)?",
    re.IGNORECASE,
)


def unsupported_recommendation_parameters(
    findings: Findings,
    *,
    metrics: list[MetricDefinition],
    user_request: str = "",
) -> list[dict[str, Any]]:
    metric_by_id = {metric.metric_id: metric for metric in metrics}
    issues: list[dict[str, Any]] = []
    for finding in findings.findings:
        allowed = _provenance_numbers(finding, metric_by_id) | _numbers(user_request)
        for parameter in _exact_parameters(finding.recommendation):
            if not _parameter_is_supported(parameter, allowed):
                issues.append(
                    {
                        "finding_id": finding.id,
                        "parameter": parameter,
                        "recommendation": finding.recommendation,
                        "code": "UNSUPPORTED_RECOMMENDATION_PARAMETER",
                    }
                )
    return issues


def _exact_parameters(text: str) -> list[str]:
    values: list[str] = []
    for match in _WINDOW_SEQUENCE.finditer(text):
        values.extend(part.strip() for part in match.group("values").split("/"))
    masked = _WINDOW_SEQUENCE.sub("", text)
    for match in _WINDOW_SINGLE.finditer(masked):
        values.append(match.group("value"))
    for pattern in (_PERCENT, _RANKING_OR_LIMIT, _CHANGE_TARGET):
        values.extend(match.group("value") for match in pattern.finditer(masked))
    return list(dict.fromkeys(values))


def _provenance_numbers(
    finding: Finding, metric_by_id: dict[str, MetricDefinition]
) -> set[Decimal]:
    texts = list(finding.evidence)
    for claim in finding.claims:
        texts.extend([claim.statement, *claim.narrative_evidence])
        for metric_id in claim.evidence_metric_ids:
            metric = metric_by_id.get(metric_id)
            if metric is None:
                continue
            texts.extend(
                str(value)
                for value in (metric.value, metric.numerator_value, metric.denominator_value)
                if value is not None
            )
    return _numbers(" ".join(texts))


def _numbers(text: str) -> set[Decimal]:
    values: set[Decimal] = set()
    for raw, unit in re.findall(rf"({_NUMBER})\s*(%)?", text or ""):
        try:
            number = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            continue
        values.add(number)
        if unit == "%":
            values.add(number / Decimal(100))
    return values


def _parameter_is_supported(parameter: str, allowed: set[Decimal]) -> bool:
    try:
        number = Decimal(parameter.replace(",", ""))
    except InvalidOperation:
        return False
    return number in allowed or number / Decimal(100) in allowed

