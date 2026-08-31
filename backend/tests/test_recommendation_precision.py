from app.schemas.findings import Findings
from app.services.metric_contract import MetricDefinition
from app.services.recommendation_precision import unsupported_recommendation_parameters


def _findings(recommendation: str) -> Findings:
    return Findings.model_validate(
        {
            "summary": "Retention is low.",
            "findings": [
                {
                    "id": "finding_retention",
                    "title": "Repeat customer share is low",
                    "evidence": ["Repeat customer share is 2.98%"],
                    "risk": "Retention risk",
                    "recommendation": recommendation,
                    "claims": [
                        {
                            "claim_id": "claim_repeat_rate",
                            "statement": "Repeat customer share is 2.98%",
                            "priority": "primary",
                            "evidence_metric_ids": ["repeat_rate"],
                        }
                    ],
                }
            ],
        }
    )


def _metrics() -> list[MetricDefinition]:
    return [
        MetricDefinition(
            metric_id="repeat_rate",
            label="Repeat customer rate",
            value=0.0298,
            aggregation="ratio",
            semantic_type="rate",
            unit_family="percentage",
            ratio_basis="fraction",
            numerator="repeat_customers",
            denominator="customers",
            numerator_value=298,
            denominator_value=10000,
            unit="%",
            definition="repeat_customers / customers",
            source_artifact="data/retention.csv",
        )
    ]


def test_recommendation_parameter_must_be_scoped_to_current_finding() -> None:
    issues = unsupported_recommendation_parameters(
        _findings("建立首购后7/30/90日复购看板"), metrics=_metrics()
    )
    assert [item["parameter"] for item in issues] == ["7", "30", "90"]


def test_supported_percentage_and_user_requested_window_are_allowed() -> None:
    assert not unsupported_recommendation_parameters(
        _findings("围绕2.98%复购率，建立30日复购观察窗口"),
        metrics=_metrics(),
        user_request="请重点关注30日复购观察窗口",
    )


def test_unrelated_metric_numbers_do_not_authorize_parameter() -> None:
    issues = unsupported_recommendation_parameters(
        _findings("建立90日复购看板"),
        metrics=_metrics(),
        user_request="分析整体订单量",
    )
    assert issues and issues[0]["code"] == "UNSUPPORTED_RECOMMENDATION_PARAMETER"
