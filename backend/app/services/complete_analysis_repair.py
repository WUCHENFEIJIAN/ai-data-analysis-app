"""Issue-scoped repair for completion-owned scalar evidence and narrative fields."""

from __future__ import annotations

import re
from typing import Any

from app.schemas.actions import CompleteAnalysisAction, CompleteAnalysisRepairResult
from app.services.metric_contract import MetricDefinition, MetricValidator


def load_repair_baseline(payload: dict[str, Any]) -> CompleteAnalysisAction:
    """Load a persisted baseline, tolerating candidates created before hard Claim guards."""

    try:
        return CompleteAnalysisAction.model_validate(payload)
    except ValueError:
        # Repair state is untrusted historical data. Keep it only as a baseline;
        # the merged candidate must pass the current strict schema before use.
        return CompleteAnalysisAction.model_construct(**payload)


METRIC_CODES = {
    "METRIC_REGISTRY_INVALID",
    "METRIC_REGISTRY_MISSING",
    "FINDING_METRIC_UNREGISTERED",
    "FINDING_METRIC_PROVENANCE_MISSING",
    "METRIC_PROVENANCE_UNVERIFIABLE",
    "METRIC_PROVENANCE_ARTIFACT_MISMATCH",
    "METRIC_PROVENANCE_GRAIN_MISMATCH",
    "METRIC_PROVENANCE_VALUE_MISMATCH",
}
CLAIM_PROVENANCE_CODES = {
    "FINDING_METRIC_UNREGISTERED",
    "FINDING_METRIC_PROVENANCE_MISSING",
    "METRIC_PROVENANCE_ARTIFACT_MISMATCH",
}
PARTIAL_REPAIR_CODES = (
    METRIC_CODES
    | CLAIM_PROVENANCE_CODES
    | {
        "UNSUPPORTED_RECOMMENDATION_PARAMETER",
    }
)


def supports_partial_repair(issues: list[dict[str, Any]]) -> bool:
    """Return whether every issue belongs to complete_analysis-owned data."""

    return bool(issues) and all(
        isinstance(issue, dict) and issue.get("code") in PARTIAL_REPAIR_CODES for issue in issues
    )


def metric_registry_validation_issues(
    metrics: list[MetricDefinition],
) -> list[dict[str, Any]]:
    issues = []
    metric_ids = {metric.metric_id for metric in metrics}
    for error in MetricValidator.issues(metrics):
        metric_id = _metric_id_from_error(error, metric_ids)
        issue = {"code": "METRIC_REGISTRY_INVALID", "error": error}
        if metric_id is not None:
            issue["metric_id"] = metric_id
        issues.append(issue)
    return issues


def preserve_issue_scoped_candidate(
    baseline: CompleteAnalysisAction,
    submitted: CompleteAnalysisAction,
    issues: list[dict[str, Any]],
) -> tuple[CompleteAnalysisAction, list[str]]:
    """Restore every completion field outside the explicit scalar/narrative issue scope."""

    baseline_payload = baseline.model_dump(mode="json")
    submitted_payload = submitted.model_dump(mode="json")
    repaired = dict(baseline_payload)
    issue_codes = {issue.get("code") for issue in issues if isinstance(issue, dict)}
    if issue_codes & METRIC_CODES:
        for field in ("scalar_metrics", "metrics"):
            repaired[field] = _repair_metric_list(
                baseline_payload.get(field, []),
                submitted_payload.get(field, []),
                issues,
            )
    if "UNSUPPORTED_RECOMMENDATION_PARAMETER" in issue_codes:
        repaired["findings"] = _repair_recommendations(
            repaired.get("findings", []),
            submitted_payload.get("findings", []),
            issues,
        )
    if issue_codes & CLAIM_PROVENANCE_CODES:
        repaired["findings"] = _repair_claim_provenance(
            repaired.get("findings", []),
            submitted_payload.get("findings", []),
            issues,
        )
    restored = [
        field
        for field in ("summary", "findings", "scalar_metrics", "metrics")
        if submitted_payload.get(field) != repaired.get(field)
    ]
    return CompleteAnalysisAction.model_validate(repaired), restored


def apply_partial_repair_result(
    baseline: CompleteAnalysisAction,
    result: CompleteAnalysisRepairResult,
    issues: list[dict[str, Any]],
) -> tuple[CompleteAnalysisAction, list[str]]:
    """Merge typed scalar, Claim, and recommendation replacements into one baseline."""

    baseline_payload = baseline.model_dump(mode="json")
    repaired = dict(baseline_payload)
    affected_metric_ids = _affected_metric_ids(issues)
    allow_new_scalar_metric = any(
        issue.get("code") == "FINDING_METRIC_PROVENANCE_MISSING" for issue in issues
    )
    metric_replacements = {
        replacement.metric_id: replacement.model_dump(mode="json")
        for replacement in result.metric_replacements
        if replacement.metric_id in affected_metric_ids
        or (allow_new_scalar_metric and replacement.metric_scope == "scalar_evidence")
    }
    if metric_replacements:
        repaired["scalar_metrics"] = _upsert_metric_replacements(
            baseline_payload.get("scalar_metrics", []), metric_replacements
        )

    affected_claims = {
        (issue.get("finding_id"), issue.get("claim_id"))
        for issue in issues
        if isinstance(issue.get("finding_id"), str) and isinstance(issue.get("claim_id"), str)
    }
    claim_replacements = {
        (replacement.finding_id, replacement.claim_id): replacement
        for replacement in result.claim_replacements
        if (replacement.finding_id, replacement.claim_id) in affected_claims
    }
    if claim_replacements:
        findings = []
        for finding in baseline_payload.get("findings", []):
            claims = []
            for claim in finding.get("claims", []):
                claim_id = claim.get("claim_id") or claim.get("id")
                replacement = claim_replacements.get((finding.get("id"), claim_id))
                if replacement is None:
                    claims.append(claim)
                    continue
                updated = dict(claim)
                if replacement.evidence_groups is not None:
                    group = replacement.evidence_groups[0]
                    updated["evidence_groups"] = [group.model_dump(mode="json")]
                    updated["evidence_metric_ids"] = list(group.metric_ids)
                    updated["evidence_artifact_paths"] = list(group.artifact_paths)
                    updated["narrative_evidence"] = list(group.narrative_evidence)
                else:
                    for field in ("evidence_metric_ids", "evidence_artifact_paths"):
                        value = getattr(replacement, field)
                        if value is not None:
                            updated[field] = value
                claims.append(updated)
            findings.append({**finding, "claims": claims})
        repaired["findings"] = findings

    affected_findings = {
        issue.get("finding_id")
        for issue in issues
        if issue.get("code") == "UNSUPPORTED_RECOMMENDATION_PARAMETER"
        and isinstance(issue.get("finding_id"), str)
    }
    recommendations = {
        replacement.finding_id: replacement.recommendation
        for replacement in result.recommendation_replacements
        if replacement.finding_id in affected_findings
    }
    if recommendations:
        repaired["findings"] = [
            {
                **finding,
                "recommendation": recommendations.get(
                    finding.get("id"), finding.get("recommendation")
                ),
            }
            for finding in repaired.get("findings", [])
        ]
    merged = CompleteAnalysisAction.model_validate(repaired)
    merged_payload = merged.model_dump(mode="json")
    changed = [
        field
        for field in ("findings", "scalar_metrics", "metrics")
        if baseline_payload.get(field) != merged_payload.get(field)
    ]
    if not changed:
        raise ValueError("partial repair result produced no effective candidate change")
    return merged, changed


def build_partial_repair_context(
    candidate: dict[str, Any],
    issues: list[dict[str, Any]],
    *,
    validation_stage: str,
    available_metrics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Select only completion-owned objects relevant to the typed repair request."""

    metrics = {
        item.get("metric_id"): item
        for item in [
            *candidate.get("scalar_metrics", []),
            *candidate.get("metrics", []),
        ]
        if isinstance(item, dict) and isinstance(item.get("metric_id"), str)
    }
    findings = {
        item.get("id"): item
        for item in candidate.get("findings", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    metric_ids = _affected_metric_ids(issues)
    affected_claims = []
    for issue in issues:
        finding_id = issue.get("finding_id")
        claim_id = issue.get("claim_id")
        finding = findings.get(finding_id)
        if not finding or not isinstance(claim_id, str):
            continue
        claim = next(
            (
                item
                for item in finding.get("claims", [])
                if (item.get("claim_id") or item.get("id")) == claim_id
            ),
            None,
        )
        if claim is not None:
            affected_claims.append(
                {
                    "finding_id": finding_id,
                    "claim_id": claim_id,
                    "statement": claim.get("statement"),
                    "evidence_metric_ids": claim.get("evidence_metric_ids", []),
                    "evidence_artifact_paths": claim.get("evidence_artifact_paths", []),
                }
            )
    if validation_stage == "recommendation_provenance":
        repair_type = "recommendation"
    elif validation_stage in {
        "metric_provenance_verification",
        "metric_provenance_declaration",
        "metric_registration",
    }:
        repair_type = "provenance"
    else:
        repair_type = "metric"
    return {
        "repair_type": repair_type,
        "validation_stage": validation_stage,
        "affected_metrics": [metrics[item] for item in sorted(metric_ids) if item in metrics],
        "available_metrics": available_metrics or [],
        "affected_claims": affected_claims,
        "effective_unlock_scope": {
            "scalar_metrics": sorted(f"scalar_metrics[{item}]" for item in metric_ids),
            "claims": sorted(
                f"findings[{issue.get('finding_id')}].claims[{issue.get('claim_id')}]"
                for issue in issues
                if isinstance(issue.get("finding_id"), str)
                and isinstance(issue.get("claim_id"), str)
            ),
            "recommendations": sorted(
                f"findings[{issue.get('finding_id')}].recommendation"
                for issue in issues
                if issue.get("code") == "UNSUPPORTED_RECOMMENDATION_PARAMETER"
                and isinstance(issue.get("finding_id"), str)
            ),
        },
    }


def complete_analysis_repair_unlock_scope(issues: list[dict[str, Any]]) -> list[str]:
    unlocked: set[str] = set()
    for issue in issues:
        code = issue.get("code")
        metric_id = issue.get("metric_id") or issue.get("metric_ref")
        finding_id = issue.get("finding_id")
        claim_id = issue.get("claim_id")
        if code in METRIC_CODES and isinstance(metric_id, str):
            unlocked.add(f"scalar_metrics[{metric_id}]")
        if (
            code in CLAIM_PROVENANCE_CODES
            and isinstance(finding_id, str)
            and isinstance(claim_id, str)
        ):
            prefix = f"findings[{finding_id}].claims[{claim_id}]"
            unlocked.add(f"{prefix}.evidence_metric_ids")
            if code == "METRIC_PROVENANCE_ARTIFACT_MISMATCH":
                unlocked.add(f"{prefix}.evidence_artifact_paths")
            if code == "FINDING_METRIC_PROVENANCE_MISSING":
                unlocked.add(f"{prefix}.evidence_groups")
        if code == "UNSUPPORTED_RECOMMENDATION_PARAMETER" and isinstance(finding_id, str):
            unlocked.add(f"findings[{finding_id}].recommendation")
    return sorted(unlocked)


def _repair_metric_list(
    baseline: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    affected = _affected_metric_ids(issues)
    submitted_by_id = {
        item.get("metric_id"): item
        for item in submitted
        if isinstance(item, dict) and isinstance(item.get("metric_id"), str)
    }
    return [
        submitted_by_id.get(item.get("metric_id"), item)
        if item.get("metric_id") in affected
        else item
        for item in baseline
    ]


def _upsert_metric_replacements(
    baseline: list[dict[str, Any]], replacements: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    result = []
    replaced: set[str] = set()
    for metric in baseline:
        metric_id = metric.get("metric_id")
        if metric_id in replacements:
            result.append(replacements[metric_id])
            replaced.add(metric_id)
        else:
            result.append(metric)
    result.extend(
        replacement for metric_id, replacement in replacements.items() if metric_id not in replaced
    )
    return result


def _repair_recommendations(
    baseline: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    affected = {
        issue.get("finding_id")
        for issue in issues
        if issue.get("code") == "UNSUPPORTED_RECOMMENDATION_PARAMETER"
    }
    submitted_by_id = {item.get("id"): item for item in submitted}
    return [
        {
            **finding,
            "recommendation": submitted_by_id.get(finding.get("id"), finding).get("recommendation"),
        }
        if finding.get("id") in affected
        else finding
        for finding in baseline
    ]


def _repair_claim_provenance(
    baseline: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    affected = {
        (issue.get("finding_id"), issue.get("claim_id"))
        for issue in issues
        if issue.get("code") in CLAIM_PROVENANCE_CODES
    }
    submitted_findings = {item.get("id"): item for item in submitted}
    result = []
    for finding in baseline:
        submitted_finding = submitted_findings.get(finding.get("id"), {})
        submitted_claims = {
            item.get("claim_id") or item.get("id"): item
            for item in submitted_finding.get("claims", [])
        }
        claims = []
        for claim in finding.get("claims", []):
            claim_id = claim.get("claim_id") or claim.get("id")
            replacement = submitted_claims.get(claim_id)
            if (finding.get("id"), claim_id) not in affected or replacement is None:
                claims.append(claim)
                continue
            updated = {**claim}
            if replacement.get("evidence_groups"):
                group = replacement["evidence_groups"][0]
                updated["evidence_groups"] = [group]
                updated["evidence_metric_ids"] = list(group.get("metric_ids", []))
                updated["evidence_artifact_paths"] = list(group.get("artifact_paths", []))
                updated["narrative_evidence"] = list(group.get("narrative_evidence", []))
            else:
                for field in ("evidence_metric_ids", "evidence_artifact_paths"):
                    if replacement.get(field) is not None:
                        updated[field] = replacement[field]
            claims.append(updated)
        result.append({**finding, "claims": claims})
    return result


def _affected_metric_ids(issues: list[dict[str, Any]]) -> set[str]:
    affected: set[str] = set()
    for issue in issues:
        for key in ("metric_id", "metric_ref"):
            value = issue.get(key)
            if isinstance(value, str):
                affected.add(value)
        for value in issue.get("missing_metric_ids", []):
            if isinstance(value, str):
                affected.add(value)
    return affected


def _metric_id_from_error(error: str, metric_ids: set[str]) -> str | None:
    prefix = error.split(":", 1)[0].strip()
    if prefix in metric_ids:
        return prefix
    for metric_id in sorted(metric_ids, key=len, reverse=True):
        if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(metric_id)}(?![A-Za-z0-9_-])", error):
            return metric_id
    return None
