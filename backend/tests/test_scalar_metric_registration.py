import json

from app.agent.orchestrator import AnalysisOrchestrator
from app.llm.mock import MockLLMProvider
from app.models import AnalysisRun
from app.schemas.actions import AgentActionResponse
from tests.test_orchestrator import prepare_run


def test_complete_analysis_rejects_metric_id_without_definition(client, settings) -> None:
    run_id = prepare_run(client, "Scalar metric contract")
    action = AgentActionResponse.model_validate(
        {
            "action": "complete_analysis",
            "summary": "A quantified finding",
            "findings": [
                {
                    "id": "finding_1",
                    "title": "Total amount",
                    "evidence": ["The total amount is 7"],
                    "risk": "The result may be stale",
                    "recommendation": "Refresh the summary",
                    "claims": [
                        {
                            "claim_id": "claim_1",
                            "statement": "The total amount is 7",
                            "evidence_metric_ids": ["total_amount"],
                        }
                    ],
                }
            ],
        }
    ).root
    orchestrator = AnalysisOrchestrator(
        client.app.state.database, settings, MockLLMProvider([]), object()
    )

    assert orchestrator._complete_analysis(run_id, action)

    with client.app.state.database.session() as session:
        run = session.get(AnalysisRun, run_id)
        rejected = [
            json.loads(event.data_json)
            for event in run.events
            if event.event_type == "analysis.action_rejected"
            and json.loads(event.data_json).get("reason") == "finding_metric_unregistered"
        ]
    assert rejected
    assert rejected[-1]["missing_metric_ids"] == ["total_amount"]
