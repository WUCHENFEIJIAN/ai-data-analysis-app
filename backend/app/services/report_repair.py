"""Progress and loop detection for Report Readiness repairs."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

COMPLETE_ANALYSIS_VALIDATION_STAGE_RANK = {
    "structured_schema": 0,
    "metric_registry": 10,
    "metric_registration": 20,
    "metric_provenance_declaration": 30,
    "metric_provenance_verification": 40,
    "recommendation_provenance": 50,
    "report_ready_artifacts": 60,
    "accepted": 70,
}


@dataclass(frozen=True)
class ReportRepairTransition:
    classification: str
    progress: bool
    reason: str
    resolved_issue_ids: tuple[str, ...]
    introduced_issue_ids: tuple[str, ...]
    artifact_changed: bool
    manifest_changed: bool
    candidate_changed: bool
    deeper_validation: bool
    stall_count: int
    oscillation_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "progress": self.progress,
            "reason": self.reason,
            "resolved_issue_ids": list(self.resolved_issue_ids),
            "introduced_issue_ids": list(self.introduced_issue_ids),
            "artifact_changed": self.artifact_changed,
            "manifest_changed": self.manifest_changed,
            "candidate_changed": self.candidate_changed,
            "deeper_validation": self.deeper_validation,
            "stall_count": self.stall_count,
            "oscillation_count": self.oscillation_count,
        }


def assess_report_repair(
    current: dict[str, Any], prior_readiness: Iterable[dict[str, Any]]
) -> ReportRepairTransition:
    """Classify a validation transition without using issue count as a progress proxy."""

    history = list(prior_readiness)
    if not history:
        return ReportRepairTransition(
            classification="initial",
            progress=False,
            reason="initial strict validation",
            resolved_issue_ids=(),
            introduced_issue_ids=tuple(sorted(_issues(current))),
            artifact_changed=False,
            manifest_changed=False,
            candidate_changed=False,
            deeper_validation=False,
            stall_count=0,
            oscillation_count=0,
        )

    previous = history[-1]
    previous_issues = _issues(previous)
    current_issues = _issues(current)
    previous_ids = set(previous_issues)
    current_ids = set(current_issues)
    resolved = tuple(sorted(previous_ids - current_ids))
    introduced = tuple(sorted(current_ids - previous_ids))
    artifact_changed = _changed(previous, current, "artifact_fingerprint")
    manifest_changed = _changed(previous, current, "manifest_fingerprint")
    candidate_changed = _candidate_changed(previous, current)
    deeper_validation = bool(resolved and introduced) and min(
        current_issues[issue_id] for issue_id in introduced
    ) > max(previous_issues[issue_id] for issue_id in resolved)

    previous_transition = previous.get("repair_transition")
    if not isinstance(previous_transition, dict):
        previous_transition = {}
    previous_stalls = _nonnegative_int(previous_transition.get("stall_count"))
    previous_oscillations = _nonnegative_int(previous_transition.get("oscillation_count"))

    signature = tuple(sorted(current_ids))
    prior_signatures = [tuple(sorted(_issues(item))) for item in history[:-1]]
    oscillating = signature in prior_signatures and signature != tuple(sorted(previous_ids))
    repair_changed = artifact_changed or manifest_changed or candidate_changed
    progress = bool(resolved) and repair_changed and (not introduced or deeper_validation)
    if oscillating:
        progress = False

    if progress:
        classification = "progressing"
        reason = (
            "previous issues resolved and the repair candidate or artifacts changed; "
            + ("validation advanced to a deeper stage" if introduced else "no replacement issues")
        )
        stall_count = 0
        oscillation_count = 0
    elif oscillating:
        classification = "oscillating"
        reason = "the repair returned to a previously observed issue state"
        stall_count = 0
        oscillation_count = previous_oscillations + 1
    elif signature == tuple(sorted(previous_ids)):
        classification = "stalled"
        reason = (
            "repair candidate or artifacts changed but no previous issue was resolved"
            if repair_changed
            else "neither the issue state nor repair candidate or artifacts changed"
        )
        stall_count = previous_stalls + 1
        oscillation_count = previous_oscillations
    elif not repair_changed:
        classification = "stalled"
        reason = "the issue state changed without any repair candidate or Artifact change"
        stall_count = previous_stalls + 1
        oscillation_count = previous_oscillations
    elif not resolved:
        classification = "stalled"
        reason = "new issues appeared without resolving a previous issue"
        stall_count = previous_stalls + 1
        oscillation_count = previous_oscillations
    else:
        classification = "changed"
        reason = (
            "repair candidate or artifacts changed and resolved previous issues, "
            "but replacement issues did not advance validation depth"
        )
        stall_count = previous_stalls + 1
        oscillation_count = previous_oscillations

    return ReportRepairTransition(
        classification=classification,
        progress=progress,
        reason=reason,
        resolved_issue_ids=resolved,
        introduced_issue_ids=introduced,
        artifact_changed=artifact_changed,
        manifest_changed=manifest_changed,
        candidate_changed=candidate_changed,
        deeper_validation=deeper_validation,
        stall_count=stall_count,
        oscillation_count=oscillation_count,
    )


def _issues(payload: dict[str, Any]) -> dict[str, int]:
    structured = payload.get("issues")
    if isinstance(structured, list):
        result: dict[str, int] = {}
        for issue in structured:
            if not isinstance(issue, dict) or not isinstance(issue.get("id"), str):
                continue
            rank = issue.get("stage_rank")
            result[issue["id"]] = rank if isinstance(rank, int) else 0
        if result:
            return result
    missing = payload.get("missing")
    if not isinstance(missing, list):
        return {}
    return {f"legacy.readiness:{item}": 0 for item in missing if isinstance(item, str)}


def _changed(previous: dict[str, Any], current: dict[str, Any], key: str) -> bool:
    if key not in previous or key not in current:
        return False
    before = previous.get(key)
    after = current.get(key)
    comparable = (isinstance(before, str) or before is None) and (
        isinstance(after, str) or after is None
    )
    return comparable and before != after


def _candidate_changed(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    keys = ("candidate_fingerprint", "candidate_manifest_fingerprint")
    before_present = any(key in previous for key in keys)
    after_present = any(key in current for key in keys)
    if not before_present or not after_present:
        return False
    before = next((previous.get(key) for key in keys if key in previous), None)
    after = next((current.get(key) for key in keys if key in current), None)
    return isinstance(before, str) and isinstance(after, str) and before != after


def _nonnegative_int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def evolve_complete_analysis_repair_state(
    previous: dict[str, Any] | None,
    candidate: dict[str, Any],
    issues: list[dict[str, Any]],
    *,
    validation_stage: str,
) -> dict[str, Any]:
    """Track the latest and best invalid complete_analysis candidate deterministically."""

    if validation_stage not in COMPLETE_ANALYSIS_VALIDATION_STAGE_RANK:
        raise ValueError(f"unknown complete_analysis validation stage: {validation_stage}")
    prior = previous if isinstance(previous, dict) else {}
    candidate_fingerprint = _json_fingerprint(candidate)
    signature = complete_analysis_issue_signature(issues)
    stage_rank = COMPLETE_ANALYSIS_VALIDATION_STAGE_RANK[validation_stage]
    previous_stage = prior.get("latest_validation_stage")
    previous_stage_rank = COMPLETE_ANALYSIS_VALIDATION_STAGE_RANK.get(
        previous_stage, -1
    )
    previous_signature = tuple(prior.get("latest_issue_signature", []))
    previous_fingerprint = prior.get("latest_candidate_fingerprint")
    previous_count = len(prior.get("latest_issues", []))
    best_count = prior.get("best_issue_count")
    if not isinstance(best_count, int) or best_count < 0:
        best_count = None
    best_stage = prior.get("best_validation_stage")
    best_stage_rank = COMPLETE_ANALYSIS_VALIDATION_STAGE_RANK.get(best_stage, -1)

    if not prior:
        classification = "initial"
        progress = False
        same_count = 0
        nonprogress_count = 0
    elif (
        validation_stage == previous_stage
        and signature == previous_signature
        and candidate_fingerprint == previous_fingerprint
    ):
        classification = "stalled"
        progress = False
        same_count = _nonnegative_int(prior.get("same_count")) + 1
        nonprogress_count = _nonnegative_int(prior.get("nonprogress_count")) + 1
    elif stage_rank > previous_stage_rank:
        classification = "progressing"
        progress = True
        same_count = 0
        nonprogress_count = 0
    elif stage_rank < previous_stage_rank:
        classification = "regressed"
        progress = False
        same_count = 0
        nonprogress_count = 0
    elif len(issues) < previous_count or set(signature) < set(previous_signature):
        classification = "progressing"
        progress = True
        same_count = 0
        nonprogress_count = 0
    elif len(issues) > previous_count:
        classification = "regressed"
        progress = False
        same_count = 0
        nonprogress_count = 0
    else:
        classification = "changed"
        progress = False
        same_count = 0
        nonprogress_count = 0

    is_best = (
        best_count is None
        or stage_rank > best_stage_rank
        or (stage_rank == best_stage_rank and len(issues) < best_count)
    )
    state = {
        "status": "invalid_pending",
        "attempt_count": _nonnegative_int(prior.get("attempt_count")) + 1,
        "latest_candidate": candidate,
        "latest_candidate_fingerprint": candidate_fingerprint,
        "latest_validation_stage": validation_stage,
        "latest_validation_stage_rank": stage_rank,
        "latest_issues": issues,
        "latest_issue_signature": list(signature),
        "best_candidate": candidate if is_best else prior.get("best_candidate"),
        "best_candidate_fingerprint": (
            candidate_fingerprint if is_best else prior.get("best_candidate_fingerprint")
        ),
        "best_validation_stage": validation_stage if is_best else best_stage,
        "best_validation_stage_rank": stage_rank if is_best else best_stage_rank,
        "best_issues": issues if is_best else prior.get("best_issues", []),
        "best_issue_signature": (
            list(signature) if is_best else prior.get("best_issue_signature", [])
        ),
        "best_issue_count": len(issues) if is_best else best_count,
        "selected_best": is_best,
        "same_count": same_count,
        "nonprogress_count": nonprogress_count,
        "invalid_action_count": 0,
        "transition": {
            "classification": classification,
            "progress": progress,
            "previous_issue_count": previous_count if prior else None,
            "current_issue_count": len(issues),
            "best_issue_count": len(issues) if is_best else best_count,
            "previous_validation_stage": previous_stage if prior else None,
            "current_validation_stage": validation_stage,
            "best_validation_stage": validation_stage if is_best else best_stage,
            "selected_best": is_best,
            "candidate_changed": candidate_fingerprint != previous_fingerprint,
            "issue_signature_changed": signature != previous_signature,
            "same_count": same_count,
            "nonprogress_count": nonprogress_count,
        },
    }
    return state


def selected_complete_analysis_repair_baseline(
    state: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Return the candidate and issues selected together for prompt and locking."""

    if not isinstance(state, dict):
        return None
    candidate = state.get("best_candidate")
    issues = state.get("best_issues")
    if not isinstance(candidate, dict) or not isinstance(issues, list):
        return None
    if not all(isinstance(issue, dict) for issue in issues):
        return None
    return candidate, issues


def complete_analysis_issue_signature(
    issues: Iterable[dict[str, Any]],
) -> tuple[str, ...]:
    keys = (
        "code",
        "artifact_path",
        "field",
        "metric_ref",
        "metric_id",
        "finding_id",
        "claim_id",
        "parameter",
    )
    return tuple(
        sorted(
            json.dumps(
                {key: issue.get(key) for key in keys if issue.get(key) is not None},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for issue in issues
            if isinstance(issue, dict)
        )
    )


report_ready_issue_signature = complete_analysis_issue_signature


def _json_fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
