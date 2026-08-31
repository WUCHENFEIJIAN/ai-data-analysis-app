"""Narrative role contract: prompt, schema, assembly, and renderer text fidelity."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.errors import ReportPipelineError
from app.services.report_editor_assembler import ReportEditorAssembler
from app.services.report_editor_prompt import ReportEditorPromptLoader
from app.services.report_editor_spec import ReportEditorNarrativeBlock, ReportEditorSpec
from app.services.report_inputs import ReportInputCollector
from app.services.report_renderer import ReportRenderer
from app.services.report_spec import NarrativeBlock
from tests.test_report_editor_pipeline import _editor_spec, prepare_editor_project


def _inputs(client, settings, project, resolver):
    with client.app.state.database.session() as session:
        return ReportInputCollector(session, resolver).collect(
            project["id"], "Analyze", "Category performance"
        )


def test_prompt_documents_narrative_role_responsibilities() -> None:
    prompt = ReportEditorPromptLoader().load()
    assert "Narrative Role 的信息职责" in prompt
    assert "不承担完整 Evidence Listing" in prompt
    assert "不要生成 supporting_narrative" in prompt
    assert "related_block_id" in prompt
    assert "不要创建 evidence_interpretation" in prompt
    assert "Callout 必须真正 Optional" in prompt
    assert "Recommendation 只讲行动" in prompt


def test_narrative_schema_accepts_related_block_and_metric_refs() -> None:
    block = ReportEditorNarrativeBlock.model_validate(
        {
            "type": "narrative",
            "text": "Growth came from customer expansion, not ticket size.",
            "display_role": "evidence_interpretation",
            "related_block_id": "data/summary.csv",
            "metric_refs": ["total_sales"],
            "claim_ids": ["claim_total"],
        }
    )
    assert block.related_block_id == "data/summary.csv"
    assert block.metric_refs == ["total_sales"]


def test_narrative_schema_still_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ReportEditorNarrativeBlock.model_validate(
            {
                "type": "narrative",
                "text": "A judgment.",
                "semantic_overlap_score": 0.9,
            }
        )


def test_assembler_does_not_inherit_section_claim_ids(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    inputs = _inputs(client, settings, project, resolver)
    draft = _editor_spec()
    draft["sections"][0]["lead"] = "The important judgment is concentration."
    draft["sections"][0]["blocks"][0]["claim_ids"] = []
    draft["sections"][0]["blocks"][0]["display_role"] = "evidence_interpretation"
    draft["sections"][0]["blocks"][0]["related_block_id"] = "analysis/findings.json"
    draft["sections"][0]["blocks"][0]["metric_refs"] = ["total_sales"]
    spec = ReportEditorAssembler().assemble(ReportEditorSpec.model_validate(draft), inputs)
    narratives = [block for block in spec.sections[0].blocks if isinstance(block, NarrativeBlock)]
    lead = next(block for block in narratives if block.display_role == "lead")
    interpretation = next(
        block for block in narratives if block.display_role == "evidence_interpretation"
    )
    assert lead.claim_ids == []
    assert interpretation.claim_ids == []
    assert interpretation.related_block_id == "analysis/findings.json"
    assert interpretation.metric_refs == ["total_sales"]


def test_renderer_does_not_append_claim_statements(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    inputs = _inputs(client, settings, project, resolver)
    draft = _editor_spec()
    draft["sections"][0]["lead"] = "Concentration is the primary judgment."
    draft["sections"][0]["blocks"][0]["text"] = "The chart shows one category dominating the total."
    draft["sections"][0]["blocks"][0]["display_role"] = "evidence_interpretation"
    spec = ReportEditorAssembler().assemble(ReportEditorSpec.model_validate(draft), inputs)
    html = ReportRenderer(resolver).render(project["id"], spec)
    assert "Concentration is the primary judgment." in html
    assert "The chart shows one category dominating the total." in html
    lead_start = html.find("data-display-role='lead'")
    lead_html = html[lead_start : html.find("</article>", lead_start) + len("</article>")]
    interp_start = html.find("data-display-role='evidence_interpretation'")
    interp_html = html[interp_start : html.find("</article>", interp_start) + len("</article>")]
    assert lead_html.count("<p>") == 1
    assert "The measured total is 100" not in lead_html
    assert "The measured total is 100" not in interp_html
    assert "data-claim-ids='claim_total'" in interp_html


def test_renderer_still_rejects_unknown_claim_refs(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    inputs = _inputs(client, settings, project, resolver)
    spec = ReportEditorAssembler().assemble(ReportEditorSpec.model_validate(_editor_spec()), inputs)
    narrative = next(
        block for block in spec.sections[0].blocks if isinstance(block, NarrativeBlock)
    )
    mutated = spec.model_copy(
        update={
            "sections": [
                spec.sections[0].model_copy(
                    update={
                        "blocks": [
                            (
                                narrative.model_copy(update={"claim_ids": ["claim_deleted"]})
                                if block is narrative
                                else block
                            )
                            for block in spec.sections[0].blocks
                        ]
                    }
                )
            ]
        }
    )
    with pytest.raises(ReportPipelineError) as caught:
        ReportRenderer(resolver).render(project["id"], mutated)
    assert caught.value.code == "report_reference_invalid"
    assert caught.value.details["reference_id"] == "claim_deleted"
