import json

import pytest

from app.core.errors import ReportPipelineError
from app.llm.mock import MockLLMProvider
from app.services.report_editor_assembler import ReportEditorAssembler
from app.services.report_editor_spec import ReportEditorSpec
from app.services.report_editorial_context import EditorialContextBuilder
from app.services.report_inputs import ReportInputCollector
from app.services.report_renderer import ReportRenderer
from app.services.report_spec import NarrativeBlock
from app.services.report_validator import ReportSpecValidator
from tests.test_report_editor_pipeline import _editor_spec, _generate, prepare_editor_project


def _inputs(client, settings, project, resolver):
    with client.app.state.database.session() as session:
        return ReportInputCollector(session, resolver).collect(
            project["id"], "Analyze", "Category performance"
        )


def test_editorial_context_exposes_existing_claims(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    inputs = _inputs(client, settings, project, resolver)
    context = EditorialContextBuilder.build(inputs)
    assert context["constraints"]["use_existing_claims_only"] is True
    assert context["claims"][0]["claim_id"] == "claim_total"
    assert context["findings"][0]["claims"][0]["statement"] == "The measured total is 100"
    assert "evidence_artifact_paths" in context["claims"][0]
    assert "evidence_metric_ids" in context["claims"][0]


def test_unknown_claim_is_stripped_before_render(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    inputs = _inputs(client, settings, project, resolver)
    draft = _editor_spec()
    draft["sections"][0]["claim_ids"] = ["claim_missing"]
    draft["sections"][0]["blocks"][0]["claim_ids"] = ["claim_missing"]
    result = ReportSpecValidator.validate(ReportEditorSpec.model_validate(draft), inputs)
    assert any(issue.code == "claim.unknown" for issue in result.issues)
    assert result.spec.sections[0].claim_ids == []
    assert result.spec.sections[0].blocks[0].claim_ids == []
    spec = ReportEditorAssembler().assemble(result.spec, inputs)
    ReportSpecValidator.validate_assembled(spec, inputs)


def test_claim_from_other_finding_is_rejected(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    findings_path = resolver.resolve(project["id"], "analysis/findings.json")
    payload = json.loads(findings_path.read_text(encoding="utf-8"))
    payload["findings"].append(
        {
            "id": "finding_2",
            "title": "Second finding",
            "evidence": ["Another verified observation"],
            "risk": "Limited",
            "recommendation": "Review later",
            "related_artifacts": [],
            "claims": [
                {
                    "claim_id": "claim_other",
                    "statement": "A second verified claim",
                    "priority": "secondary",
                    "strength": 0.6,
                    "evidence_metric_ids": ["total_sales"],
                }
            ],
        }
    )
    findings_path.write_text(json.dumps(payload), encoding="utf-8")
    inputs = _inputs(client, settings, project, resolver)
    draft = _editor_spec()
    draft["sections"][0]["finding_refs"] = ["finding_1"]
    draft["sections"][0]["claim_ids"] = ["claim_other"]
    draft["sections"][0]["blocks"][0]["claim_ids"] = ["claim_other"]
    result = ReportSpecValidator.validate(ReportEditorSpec.model_validate(draft), inputs)
    assert any(issue.code == "claim.wrong_finding" for issue in result.issues)
    assert "claim_other" not in result.spec.sections[0].claim_ids
    assert "claim_other" not in result.spec.sections[0].blocks[0].claim_ids


def test_composite_insight_ids_are_not_assembled(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    inputs = _inputs(client, settings, project, resolver)
    draft = _editor_spec()
    draft["sections"][0]["blocks"][0]["composite_insight_ids"] = ["insight_made_up"]
    result = ReportSpecValidator.validate(ReportEditorSpec.model_validate(draft), inputs)
    assert any(issue.code == "block.composite_insight_not_supported" for issue in result.issues)
    assert result.spec.sections[0].blocks[0].composite_insight_ids == []
    spec = ReportEditorAssembler().assemble(result.spec, inputs)
    narrative = next(
        block for block in spec.sections[0].blocks if isinstance(block, NarrativeBlock)
    )
    assert narrative.composite_insight_ids == []
    ReportSpecValidator.validate_assembled(spec, inputs)


def test_recommendation_unknown_claim_is_rejected(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    inputs = _inputs(client, settings, project, resolver)
    draft = _editor_spec()
    draft["sections"][0]["blocks"].append(
        {
            "type": "recommendations",
            "items": [
                {
                    "text": "Watch the concentrated category",
                    "priority": "near_term",
                    "source_finding_ids": ["finding_1"],
                    "source_claim_ids": ["claim_missing"],
                }
            ],
        }
    )
    result = ReportSpecValidator.validate(ReportEditorSpec.model_validate(draft), inputs)
    assert any(issue.code == "claim.unknown" for issue in result.issues)
    rec = next(
        block
        for section in result.spec.sections
        for block in section.blocks
        if block.type == "recommendations"
    )
    assert rec.items[0].source_claim_ids == []
    assert rec.items[0].source_finding_ids == ["finding_1"]


def test_renderer_raises_explicit_invalid_reference(client, settings) -> None:
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
                            narrative.model_copy(update={"claim_ids": ["claim_deleted"]})
                            if block is narrative
                            else block
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
    assert caught.value.details["section"] == "section_1"
    assert caught.value.details["block"] == "narrative"
    assert caught.value.details["reference_type"] == "claim_id"
    assert caught.value.details["reference_id"] == "claim_deleted"


@pytest.mark.asyncio
async def test_unknown_claim_in_editor_output_does_not_internal_error(client, settings) -> None:
    project, resolver = prepare_editor_project(client, settings, with_chart=False)
    draft = _editor_spec()
    draft["sections"][0]["blocks"][0]["claim_ids"] = ["claim_invented"]
    path = await _generate(
        client, settings, project, resolver, MockLLMProvider([draft, draft])
    )
    html = resolver.resolve(project["id"], path).read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "Analysis failed because of an internal error" not in html
