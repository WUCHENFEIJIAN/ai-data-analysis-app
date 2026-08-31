from __future__ import annotations

from app.schemas.findings import Findings
from app.services.metric_contract import MetricDefinition
from app.services.report_evidence import ReportEvidenceManifest
from app.services.report_inputs import ArtifactEntry, ReportInputs
from app.services.report_metric_fidelity import build_visual_context, eligible_visual_contexts


def _metric(metric_id: str, path: str, field: str) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        metric_scope="reusable_measure",
        label=metric_id,
        value=None,
        aggregation="sum",
        semantic_type="measure",
        unit_family="quantity",
        unit="unit",
        definition=f"Sum of {field}",
        source_artifact=path,
        source_field=field,
        grain="category",
    )


def _entry(path: str, metric_id: str) -> ArtifactEntry:
    return ArtifactEntry(
        id=f"artifact_{metric_id}",
        path=path,
        kind="csv",
        sha256="a" * 64,
        media_type="text/csv",
        size_bytes=20,
        report_ready=True,
        structure={
            "record_kind": "table",
            "columns": [
                {"name": "category", "role": "dimension", "type": "string"},
                {"name": "amount", "role": "measure", "type": "number", "metric_ref": metric_id},
            ],
            "row_count": 2,
        },
    )


def _inputs(findings: dict, *, paths: list[tuple[str, str]]) -> ReportInputs:
    metrics = [_metric(metric_id, path, "amount") for path, metric_id in paths]
    return ReportInputs(
        analysis_topic="Neutral visual eligibility",
        title="Neutral visual eligibility",
        subtitle=None,
        requested_style=None,
        user_request="Check visual evidence eligibility",
        dataset_profile={},
        analysis_plan={},
        findings=Findings.model_validate({"summary": "Test", "findings": [findings]}),
        metrics=metrics,
        catalog=[_entry(path, metric_id) for path, metric_id in paths],
        evidence_manifest=ReportEvidenceManifest(schema_version="1.0"),
    )


def _claim(
    *,
    claim_id: str,
    statement: str,
    artifact_paths: list[str] | None = None,
    metric_ids: list[str] | None = None,
    report_role: str = "business_insight",
    narrative_role: str = "context",
) -> dict:
    return {
        "claim_id": claim_id,
        "statement": statement,
        "priority": "primary",
        "report_role": report_role,
        "narrative_role": narrative_role,
        "evidence_artifact_paths": artifact_paths or [],
        "evidence_metric_ids": metric_ids or [],
    }


def test_nonquantitative_business_claim_with_explicit_artifact_binding_is_eligible() -> None:
    inputs = _inputs(
        {
            "id": "finding_a",
            "title": "Category structure",
            "evidence": ["The category structure differs."],
            "risk": "Scope is limited.",
            "recommendation": "Review category mix.",
            "related_artifacts": [],
            "claims": [
                _claim(
                    claim_id="claim_a",
                    statement="Category structure differs.",
                    artifact_paths=["data/category.csv"],
                )
            ],
        },
        paths=[("data/category.csv", "category_amount")],
    )

    assert len(build_visual_context(inputs)) == 2
    assert len(eligible_visual_contexts(inputs)) == 2


def test_nonquantitative_claim_without_artifact_binding_is_not_eligible() -> None:
    inputs = _inputs(
        {
            "id": "finding_b",
            "title": "Category structure",
            "evidence": ["The category structure differs."],
            "risk": "Scope is limited.",
            "recommendation": "Review category mix.",
            "related_artifacts": ["data/category.csv"],
            "claims": [_claim(claim_id="claim_b", statement="Category structure differs.")],
        },
        paths=[("data/category.csv", "category_amount")],
    )

    assert len(build_visual_context(inputs)) == 2
    assert eligible_visual_contexts(inputs) == []


def test_quantitative_claim_with_invalid_metric_binding_does_not_bypass_evidence_validation(
) -> None:
    inputs = _inputs(
        {
            "id": "finding_c",
            "title": "Category result",
            "evidence": ["The measured value is 10."],
            "risk": "Scope is limited.",
            "recommendation": "Review the result.",
            "related_artifacts": [],
            "claims": [
                _claim(
                    claim_id="claim_c",
                    statement="The measured value is 10.",
                    artifact_paths=["data/category.csv"],
                    metric_ids=["missing_metric"],
                )
            ],
        },
        paths=[("data/category.csv", "category_amount")],
    )

    assert eligible_visual_contexts(inputs) == []


def test_internal_diagnostic_finding_does_not_support_business_visuals() -> None:
    inputs = _inputs(
        {
            "id": "finding_d",
            "title": "Data quality check",
            "evidence": ["The source was inspected."],
            "risk": "This is an internal check.",
            "recommendation": "Keep the check.",
            "related_artifacts": [],
            "claims": [
                _claim(
                    claim_id="claim_d",
                    statement="The source was inspected.",
                    artifact_paths=["data/category.csv"],
                    narrative_role="data_quality",
                )
            ],
        },
        paths=[("data/category.csv", "category_amount")],
    )

    assert eligible_visual_contexts(inputs) == []


def test_quantitative_claim_does_not_unlock_unrelated_finding_artifacts() -> None:
    inputs = _inputs(
        {
            "id": "finding_e",
            "title": "Scoped result",
            "evidence": ["Category A is 10."],
            "risk": "Scope is limited.",
            "recommendation": "Review the bound artifact.",
            "related_artifacts": ["data/bound.csv", "data/unrelated.csv"],
            "claims": [
                _claim(
                    claim_id="claim_e",
                    statement="Category A is 10.",
                    artifact_paths=["data/bound.csv"],
                    metric_ids=["bound_amount"],
                )
            ],
        },
        paths=[
            ("data/bound.csv", "bound_amount"),
            ("data/unrelated.csv", "unrelated_amount"),
        ],
    )

    eligible_paths = {item["data_ref"] for item in eligible_visual_contexts(inputs)}
    assert eligible_paths == {"data/bound.csv"}



