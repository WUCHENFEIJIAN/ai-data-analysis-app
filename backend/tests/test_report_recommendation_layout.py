from app.services.report_editor_assembler import ReportEditorAssembler
from app.services.report_editor_spec import ReportEditorSpec
from app.services.report_spec import RecommendationBlock
from tests.test_report_metric_fidelity import _inputs


def test_assembler_collects_recommendations_into_final_section() -> None:
    draft = ReportEditorSpec.model_validate(
        {
            "headline": "Verified report",
            "summary": "Verified summary",
            "sections": [
                {
                    "title": "Finding A",
                    "finding_refs": ["finding_visual"],
                    "blocks": [
                        {"type": "narrative", "text": "Finding A context"},
                        {
                            "type": "recommendations",
                            "items": [
                                {
                                    "text": "Take action A",
                                    "priority": "immediate",
                                    "source_finding_ids": ["finding_visual"],
                                }
                            ],
                        },
                    ],
                },
                {
                    "title": "Finding B",
                    "finding_refs": ["finding_visual"],
                    "blocks": [
                        {"type": "narrative", "text": "Finding B context"},
                        {
                            "type": "recommendations",
                            "items": [
                                {
                                    "text": "Monitor result B",
                                    "priority": "monitor",
                                    "source_claim_ids": ["claim_visual"],
                                }
                            ],
                        },
                    ],
                },
            ],
        }
    )

    spec = ReportEditorAssembler().assemble(draft, _inputs())

    assert spec.sections[-1].title == "行动建议"
    assert all(
        not any(block.type == "recommendations" for block in section.blocks)
        for section in spec.sections[:-1]
    )
    recommendation = next(
        block for block in spec.sections[-1].blocks if isinstance(block, RecommendationBlock)
    )
    assert [item.text for item in recommendation.items] == [
        "Take action A",
        "Monitor result B",
    ]
    assert [item.id for item in recommendation.items] == ["rec_3_1", "rec_3_2"]
