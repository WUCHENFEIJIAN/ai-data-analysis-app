import pytest
from pydantic import ValidationError

from app.schemas.actions import CompleteAnalysisAction
from app.services.complete_analysis_repair import preserve_issue_scoped_candidate
from app.services.report_repair import (
    evolve_complete_analysis_repair_state,
    selected_complete_analysis_repair_baseline,
)


def _metric(metric_id: str, value: float = 1.0) -> dict:
    return {
        "metric_id": metric_id,
        "label": metric_id,
        "value": value,
        "aggregation": "sum",
        "semantic_type": "measure",
        "unit_family": "currency",
        "definition": f"Sum of {metric_id}",
        "source_artifact": "data/neutral.csv",
    }


def _finding(index: int) -> dict:
    return {
        "id": f"finding_{index}",
        "title": f"Finding {index}",
        "evidence": [f"Evidence {index}"],
        "risk": f"Risk {index}",
        "recommendation": f"Monitor category {index}",
        "claims": [
            {
                "claim_id": f"claim_{index}",
                "statement": f"Metric {index} is stable",
                "evidence_metric_ids": [f"metric_{index}"],
            }
        ],
    }


def _candidate(finding_count: int = 5, metric_count: int = 10):
    return CompleteAnalysisAction.model_validate(
        {
            "action": "complete_analysis",
            "summary": "Neutral analysis",
            "findings": [_finding(index) for index in range(finding_count)],
            "metrics": [_metric(f"metric_{index}") for index in range(metric_count)],
        }
    )


def test_metric_repair_preserves_all_unrelated_findings_and_metrics():
    baseline = _candidate()
    submitted_payload = baseline.model_dump(mode="json")
    submitted_payload["findings"] = [submitted_payload["findings"][0]]
    submitted_payload["metrics"] = [
        {**metric, "value": 99.0} for metric in submitted_payload["metrics"]
    ]
    submitted = CompleteAnalysisAction.model_validate(submitted_payload)

    repaired, restored = preserve_issue_scoped_candidate(
        baseline,
        submitted,
        [{"code": "METRIC_REGISTRY_INVALID", "metric_id": "metric_3"}],
    )

    assert len(repaired.findings) == 5
    assert len(repaired.metrics) == 10
    assert repaired.metrics[3].value == 99.0
    assert all(metric.value == 1.0 for metric in repaired.metrics if metric.metric_id != "metric_3")
    assert "findings" in restored
    assert "metrics" in restored


def test_recommendation_repair_preserves_candidate_shape():
    baseline = _candidate()
    submitted_payload = baseline.model_dump(mode="json")
    submitted_payload["findings"] = [submitted_payload["findings"][0]]
    submitted_payload["findings"][0]["recommendation"] = "Monitor in phases"
    submitted_payload["metrics"] = submitted_payload["metrics"][:1]
    submitted = CompleteAnalysisAction.model_validate(submitted_payload)

    repaired, _ = preserve_issue_scoped_candidate(
        baseline,
        submitted,
        [
            {
                "code": "UNSUPPORTED_RECOMMENDATION_PARAMETER",
                "finding_id": "finding_0",
                "parameter": "30",
            }
        ],
    )

    assert len(repaired.findings) == 5
    assert sum(len(finding.claims) for finding in repaired.findings) == 5
    assert len(repaired.metrics) == 10
    assert repaired.findings[0].recommendation == "Monitor in phases"
    assert repaired.findings[1].recommendation == baseline.findings[1].recommendation


def test_best_candidate_survives_regression():
    first = evolve_complete_analysis_repair_state(
        None,
        {"candidate": "a1"},
        [{"code": f"ISSUE_{index}"} for index in range(6)],
        validation_stage="report_ready_artifacts",
    )
    second = evolve_complete_analysis_repair_state(
        first,
        {"candidate": "a2"},
        [{"code": "ISSUE_0"}],
        validation_stage="report_ready_artifacts",
    )
    third = evolve_complete_analysis_repair_state(
        second,
        {"candidate": "a3"},
        [{"code": f"ISSUE_{index}"} for index in range(4)],
        validation_stage="report_ready_artifacts",
    )

    assert second["transition"]["classification"] == "progressing"
    assert third["transition"]["classification"] == "regressed"
    assert third["best_candidate"] == {"candidate": "a2"}
    assert third["best_issue_count"] == 1


def test_only_identical_candidate_and_issue_signature_stalls():
    first = evolve_complete_analysis_repair_state(
        None,
        {"candidate": "a1"},
        [{"code": "METRIC_REGISTRY_INVALID", "metric_id": "metric_x"}],
        validation_stage="metric_registry",
    )
    changed = evolve_complete_analysis_repair_state(
        first,
        {"candidate": "a2"},
        [{"code": "METRIC_REGISTRY_INVALID", "metric_id": "metric_x"}],
        validation_stage="metric_registry",
    )
    identical = evolve_complete_analysis_repair_state(
        changed,
        {"candidate": "a2"},
        [{"code": "METRIC_REGISTRY_INVALID", "metric_id": "metric_x"}],
        validation_stage="metric_registry",
    )

    assert changed["transition"]["classification"] == "changed"
    assert changed["transition"]["nonprogress_count"] == 0
    assert identical["transition"]["classification"] == "stalled"
    assert identical["transition"]["nonprogress_count"] == 1


def test_deeper_provenance_stage_wins_even_with_twenty_issues():
    declaration = evolve_complete_analysis_repair_state(
        None,
        {"candidate": "declaration"},
        [{"code": "FINDING_METRIC_PROVENANCE_MISSING"}],
        validation_stage="metric_provenance_declaration",
    )
    verification_issues = [
        {"code": "METRIC_PROVENANCE_UNVERIFIABLE", "metric_id": f"metric_{index}"}
        for index in range(20)
    ]
    verification = evolve_complete_analysis_repair_state(
        declaration,
        {"candidate": "verification"},
        verification_issues,
        validation_stage="metric_provenance_verification",
    )

    assert verification["transition"]["classification"] == "progressing"
    assert verification["best_candidate"] == {"candidate": "verification"}
    assert verification["best_issues"] == verification_issues
    assert verification["best_validation_stage"] == "metric_provenance_verification"
    assert verification["selected_best"] is True


def test_same_provenance_stage_prefers_three_issues_over_ten():
    ten = evolve_complete_analysis_repair_state(
        None,
        {"candidate": "ten"},
        [{"code": f"ISSUE_{index}"} for index in range(10)],
        validation_stage="metric_provenance_verification",
    )
    three_issues = [{"code": f"ISSUE_{index}"} for index in range(3)]
    three = evolve_complete_analysis_repair_state(
        ten,
        {"candidate": "three"},
        three_issues,
        validation_stage="metric_provenance_verification",
    )

    assert three["best_candidate"] == {"candidate": "three"}
    assert three["best_issues"] == three_issues
    assert three["selected_best"] is True


def test_later_validation_stage_wins_even_with_more_issues():
    early = evolve_complete_analysis_repair_state(
        None,
        {"candidate": "metric"},
        [{"code": "METRIC_REGISTRY_INVALID"}],
        validation_stage="metric_registry",
    )
    later_issues = [
        {"code": "REPORT_READY_MEASURE_NOT_REUSABLE", "field": f"measure_{index}"}
        for index in range(5)
    ]
    later = evolve_complete_analysis_repair_state(
        early,
        {"candidate": "report-ready"},
        later_issues,
        validation_stage="report_ready_artifacts",
    )

    assert later["transition"]["classification"] == "progressing"
    assert later["best_candidate"] == {"candidate": "report-ready"}
    assert later["best_issues"] == later_issues
    assert later["best_validation_stage"] == "report_ready_artifacts"


def test_same_stage_prefers_fewer_issues_and_retains_it_on_regression():
    five = evolve_complete_analysis_repair_state(
        None,
        {"candidate": "five"},
        [{"code": f"ISSUE_{index}"} for index in range(5)],
        validation_stage="report_ready_artifacts",
    )
    two_issues = [{"code": f"ISSUE_{index}"} for index in range(2)]
    two = evolve_complete_analysis_repair_state(
        five,
        {"candidate": "two"},
        two_issues,
        validation_stage="report_ready_artifacts",
    )
    regressed = evolve_complete_analysis_repair_state(
        two,
        {"candidate": "four"},
        [{"code": f"ISSUE_{index}"} for index in range(4)],
        validation_stage="report_ready_artifacts",
    )

    assert two["best_candidate"] == {"candidate": "two"}
    assert regressed["transition"]["classification"] == "regressed"
    assert regressed["best_candidate"] == {"candidate": "two"}
    assert regressed["best_issues"] == two_issues


def test_prompt_issue_and_locking_baseline_are_selected_as_one_pair():
    early = evolve_complete_analysis_repair_state(
        None,
        {"candidate": "early"},
        [{"code": "METRIC_REGISTRY_INVALID"}],
        validation_stage="metric_registry",
    )
    later_issues = [{"code": "REPORT_READY_MEASURE_NOT_REUSABLE", "metric_ref": "measure_x"}]
    later = evolve_complete_analysis_repair_state(
        early,
        {"candidate": "later"},
        later_issues,
        validation_stage="report_ready_artifacts",
    )

    assert selected_complete_analysis_repair_baseline(later) == (
        {"candidate": "later"},
        later_issues,
    )


def test_duplicate_metric_is_rejected_before_repair():
    baseline_payload = _candidate(finding_count=1, metric_count=2).model_dump(mode="json")
    duplicate = dict(baseline_payload["metrics"][0])
    baseline_payload["metrics"].append(duplicate)

    with pytest.raises(ValidationError, match="unique metric_id"):
        CompleteAnalysisAction.model_validate(baseline_payload)


def test_artifact_mismatch_unlocks_only_affected_claim_provenance():
    baseline_payload = _candidate(finding_count=2, metric_count=2).model_dump(mode="json")
    for index, finding in enumerate(baseline_payload["findings"]):
        finding["claims"][0]["evidence_artifact_paths"] = [f"data/old_{index}.csv"]
    baseline = CompleteAnalysisAction.model_validate(baseline_payload)
    submitted_payload = baseline.model_dump(mode="json")
    submitted_payload["metrics"][0]["source_artifact"] = "data/actual.csv"
    submitted_payload["metrics"][1]["value"] = 99
    submitted_payload["findings"][0]["claims"][0]["evidence_artifact_paths"] = ["data/actual.csv"]
    # Keep the metric binding valid; this case isolates artifact-path repair.
    submitted_payload["findings"][0]["recommendation"] = "Replace unrelated recommendation"
    submitted_payload["findings"][1]["claims"][0]["evidence_artifact_paths"] = [
        "data/unrelated.csv"
    ]
    submitted = CompleteAnalysisAction.model_validate(submitted_payload)

    repaired, restored = preserve_issue_scoped_candidate(
        baseline,
        submitted,
        [
            {
                "code": "METRIC_PROVENANCE_ARTIFACT_MISMATCH",
                "metric_id": "metric_0",
                "finding_id": "finding_0",
                "claim_id": "claim_0",
            }
        ],
    )

    assert repaired.metrics[0].source_artifact == "data/actual.csv"
    assert repaired.metrics[1] == baseline.metrics[1]
    assert repaired.findings[0].claims[0].evidence_artifact_paths == ["data/actual.csv"]
    assert repaired.findings[0].claims[0].evidence_metric_ids == ["metric_0"]
    assert repaired.findings[0].recommendation == baseline.findings[0].recommendation
    assert repaired.findings[1] == baseline.findings[1]
    assert "findings" in restored
    assert "metrics" in restored
