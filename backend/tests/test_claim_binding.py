import json

import pytest
from pydantic import ValidationError
from test_reports import prepare_report_inputs

from app.services.report_inputs import ReportInputCollector
from app.services.report_readiness import ReportReadinessService
from app.skills.loader import SkillLoader


def _claims_payload() -> list[dict[str, object]]:
    return [
        {
            "claim_id": "claim_01_east",
            "statement": "East contributes the measured sales",
            "priority": "primary",
            "evidence_metric_ids": ["total_sales"],
        },
        {
            "claim_id": "claim_02_other",
            "statement": "The second claim requires a separate metric",
            "priority": "secondary",
            "evidence_metric_ids": ["other_metric"],
        },
    ]


def _input_with_claims(client, settings):
    project, resolver = prepare_report_inputs(client, settings)
    findings_path = resolver.resolve(project["id"], "analysis/findings.json")
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    findings["findings"][0]["claims"] = _claims_payload()
    findings_path.write_text(json.dumps(findings), encoding="utf-8")
    manifest_path = resolver.resolve(project["id"], "analysis/report_evidence.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["kpis"][0]["supports_claim_ids"] = ["claim_01_east"]
    manifest["artifacts"][0]["supports_claim_ids"] = [
        "claim_01_east",
        "claim_02_other",
    ]
    manifest["artifacts"][0]["chart"]["supports_claim_ids"] = [
        "claim_01_east",
        "claim_02_other",
    ]
    manifest["metrics"].append(
        {
            "metric_id": "other_metric",
            "label": "Other metric",
            "value": 1,
            "aggregation": "sum",
            "semantic_type": "measure",
            "unit_family": "quantity",
            "unit": "",
            "definition": "Precomputed other metric",
            "source_artifact": "data/evidence.csv",
        }
    )
    # The fixture chart only carries `sales`, so the second claim is
    # intentionally not provable by that visual.
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return project, resolver


def test_claim_id_alias_is_supported() -> None:
    from app.schemas.findings import Claim

    assert Claim(claim_id="claim_1", statement="A").id == "claim_1"
    assert Claim(id="claim_2", statement="B").claim_id == "claim_2"


def test_atomic_claim_rejects_two_independent_evidence_groups() -> None:
    from app.schemas.findings import Claim

    with pytest.raises(ValueError, match="at most 1 item"):
        Claim(
            claim_id="claim_split_me",
            statement="Two independent conclusions were combined",
            evidence_groups=[
                {"metric_ids": ["metric_a"]},
                {"metric_ids": ["metric_b"]},
            ],
        )


def test_report_inputs_identify_the_unsupported_kpi(client, settings) -> None:
    project, resolver = prepare_report_inputs(client, settings)
    manifest_path = resolver.resolve(project["id"], "analysis/report_evidence.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsupported = {
        **manifest["kpis"][0],
        "id": "category_value_as_total",
        "metric": "sales",
    }
    second_unsupported = {
        **unsupported,
        "id": "second_category_value_as_total",
    }
    manifest["kpis"].extend([unsupported, second_unsupported])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver, SkillLoader(settings.skill_root)).collect(
            project["id"], "Analyze", "Report"
        )
    assert {item.metric_id for item in inputs.metrics} >= {"total_sales", "sales"}


def test_readiness_exposes_each_semantic_violation_as_a_stable_issue(client, settings) -> None:
    project, resolver = prepare_report_inputs(client, settings)
    manifest_path = resolver.resolve(project["id"], "analysis/report_evidence.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first = {
        **manifest["kpis"][0],
        "id": "category_value_as_total",
        "metric": "sales",
    }
    second = {**first, "id": "second_category_value_as_total"}
    manifest["kpis"].extend([first, second])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with client.app.state.database.session() as session:
        ReportReadinessService(session, resolver, SkillLoader(settings.skill_root)).check_project(
            project["id"], "Report"
        )

    # Readiness may still inspect the historical contract, but it is unused by
    # report generation. Collecting inputs must succeed.
    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver, SkillLoader(settings.skill_root)).collect(
            project["id"], "Analyze", "Report"
        )
    assert inputs.findings.findings


def test_report_inputs_identify_chart_with_incompatible_metrics(client, settings) -> None:
    project, resolver = prepare_report_inputs(client, settings)
    manifest_path = resolver.resolve(project["id"], "analysis/report_evidence.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["metrics"].append(
        {
            "metric_id": "row_count",
            "label": "Row count",
            "value": 1,
            "aggregation": "count",
            "semantic_type": "count",
            "unit_family": "count",
            "count_semantics": "row_count",
            "is_distinct": False,
            "definition": "Count of rows",
            "source_artifact": "data/evidence.csv",
        }
    )
    manifest["artifacts"][0]["chart"]["series"].append(
        {
            "field": "sales",
            "label": "Rows",
            "metric": "row_count",
            "format": "integer",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver, SkillLoader(settings.skill_root)).collect(
            project["id"], "Analyze", "Report"
        )
    assert any(item.metric_id == "row_count" for item in inputs.metrics)


def test_report_inputs_allow_joint_claim_evidence_coverage(client, settings) -> None:
    project, resolver = prepare_report_inputs(client, settings)
    findings_path = resolver.resolve(project["id"], "analysis/findings.json")
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    joint_claim = findings["findings"][0]["claims"][0]
    joint_claim["evidence_metric_ids"] = ["total_sales", "sales"]
    joint_claim["evidence_artifact_paths"] = ["data/evidence.csv"]
    findings_path.write_text(json.dumps(findings), encoding="utf-8")

    manifest_path = resolver.resolve(project["id"], "analysis/report_evidence.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["supports_claim_ids"] = ["claim_total", "claim_east"]
    manifest["artifacts"][0]["chart"]["supports_claim_ids"] = [
        "claim_total",
        "claim_east",
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with client.app.state.database.session() as session:
        inputs = ReportInputCollector(session, resolver, SkillLoader(settings.skill_root)).collect(
            project["id"], "Analyze", "Report"
        )

    assert inputs.evidence_manifest.kpis[0].metric == "total_sales"
    assert inputs.evidence_manifest.artifacts[0].chart.series[0].metric == "sales"


def test_quantitative_business_claim_requires_metric_evidence() -> None:
    from app.schemas.findings import Claim

    with pytest.raises(
        ValidationError, match="quantitative business claims must declare at least one"
    ):
        Claim.model_validate(
            {
                "claim_id": "claim_numeric_without_metric",
                "statement": "Team coverage is 80%.",
            }
        )


def test_quantitative_business_claim_with_metric_evidence_is_valid() -> None:
    from app.schemas.findings import Claim

    claim = Claim.model_validate(
        {
            "claim_id": "claim_numeric_with_metric",
            "statement": "Team coverage is 80%.",
            "evidence_metric_ids": ["team_coverage"],
        }
    )

    assert claim.is_quantitative is True
