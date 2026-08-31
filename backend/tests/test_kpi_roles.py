import pytest

from app.services.report_evidence import EvidenceKpi


def _values() -> dict:
    return {
        "id": "metric_card",
        "label": "Metric card",
        "metric": "metric_x",
        "artifact_path": "data/metric.json",
        "selector": {"type": "json", "path": ["value"]},
        "format": "number",
        "purpose": "Show the declared metric",
    }


def test_overview_kpi_can_omit_claim_binding() -> None:
    kpi = EvidenceKpi(**_values(), role="overview")
    assert kpi.finding_ids == []
    assert kpi.supports_claim_ids == []
    assert kpi.presentation_roles == ["overview"]


def test_kpi_can_be_overview_and_claim_evidence() -> None:
    kpi = EvidenceKpi(
        **_values(),
        presentation_roles=["overview", "evidence"],
        finding_ids=["finding_1"],
        supports_claim_ids=["claim_1"],
        display_label="Metric",
        definition_note="Strict aggregation definition",
    )
    assert kpi.role == "overview"
    assert kpi.presentation_roles == ["overview", "evidence"]
    assert kpi.display_label == "Metric"


def test_evidence_kpi_requires_claim_binding() -> None:
    with pytest.raises(ValueError, match="requires finding_ids and supports_claim_ids"):
        EvidenceKpi(**_values(), role="evidence", finding_ids=["finding_1"])


def test_kpi_without_presentation_role_is_invalid() -> None:
    with pytest.raises(ValueError, match="presentation role"):
        EvidenceKpi(**_values())
