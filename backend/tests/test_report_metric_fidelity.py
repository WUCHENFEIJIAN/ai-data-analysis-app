"""Generic Visual ↔ Interpretation metric provenance tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.errors import ReportPipelineError
from app.schemas.findings import Findings
from app.services.metric_contract import MetricDefinition
from app.services.presentation_metadata import PresentationMetadataResolver
from app.services.report_editor_assembler import ReportEditorAssembler
from app.services.report_editor_spec import ReportEditorSpec
from app.services.report_editorial_context import EditorialContextBuilder
from app.services.report_evidence import ReportEvidenceManifest
from app.services.report_inputs import ArtifactEntry, ReportInputs
from app.services.report_renderer import ReportRenderer
from app.services.report_validator import ReportSpecValidator


def _metric(metric_id: str, label: str | None = None) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        metric_scope="reusable_measure",
        label=label or metric_id,
        value=10,
        aggregation="sum",
        semantic_type="measure",
        unit_family="quantity",
        unit="unit",
        definition=f"Verified {metric_id}",
        source_artifact="data/visual.csv",
        source_field=f"value_{metric_id.rsplit('_', 1)[-1]}",
    )


def _inputs(*, claim_metrics: list[str] | None = None) -> ReportInputs:
    claim = {
        "claim_id": "claim_visual",
        "statement": "Metric X records 10 and Metric Y records 10 in the verified pattern.",
        "priority": "primary",
        "strength": 0.9,
        "evidence_metric_ids": claim_metrics or ["metric_x"],
        "evidence_artifact_paths": ["data/visual.csv"],
    }
    metrics = [_metric("metric_x", "Metric X"), _metric("metric_y", "Metric Y")]
    manifest = ReportEvidenceManifest.model_validate(
        {
            "schema_version": "1.0",
            "metrics": [item.model_dump() for item in metrics],
            "artifacts": [
                {
                    "artifact_path": "data/visual.csv",
                    "usage": "visual_source",
                    "finding_ids": ["finding_visual"],
                    "purpose": "Compare verified metrics",
                    "chart": {
                        "chart_type": "bar",
                        "title": "Metric comparison",
                        "x_field": "category_a",
                        "series": [
                            {
                                "field": "value_x",
                                "label": "Metric X",
                                "metric": "metric_x",
                            },
                            {
                                "field": "value_y",
                                "label": "Metric Y",
                                "metric": "metric_y",
                            },
                        ],
                        "source_caption": "Source: visual.csv",
                    },
                }
            ],
        }
    )
    return ReportInputs(
        analysis_topic="Neutral metric review",
        title="Neutral metric review",
        subtitle=None,
        requested_style=None,
        user_request="Review the measured pattern",
        dataset_profile={"file_count": 1},
        analysis_plan={"objective": "Compare metrics"},
        findings=Findings.model_validate(
            {
                "summary": "A verified pattern exists.",
                "findings": [
                    {
                        "id": "finding_visual",
                        "title": "Verified metric pattern",
                        "evidence": ["The visual artifact contains the measured values."],
                        "risk": "Interpretation depends on the current scope.",
                        "recommendation": "Review the measured pattern.",
                        "related_artifacts": ["data/visual.csv"],
                        "claims": [claim],
                    }
                ],
            }
        ),
        metrics=metrics,
        catalog=[
            ArtifactEntry(
                id="artifact_visual",
                path="data/visual.csv",
                kind="csv",
                sha256="a" * 64,
                media_type="text/csv",
                size_bytes=20,
                report_ready=True,
                structure={
                    "record_kind": "table",
                    "columns": [
                        {
                            "name": "category_a",
                            "display_label": "Category A",
                            "role": "dimension",
                        },
                        {
                            "name": "value_x",
                            "display_label": "Metric X",
                            "role": "measure",
                            "metric_ref": "metric_x",
                        },
                        {
                            "name": "value_y",
                            "display_label": "Metric Y",
                            "role": "measure",
                            "metric_ref": "metric_y",
                        },
                    ],
                    "row_count": 2,
                },
            )
        ],
        evidence_manifest=manifest,
    )


def _draft(
    metric_refs: list[str],
    *,
    visual_fields: list[str] | None = None,
    claim_ids: list[str] | None = None,
    related_block_ids: list[str] | None = None,
    section_role: str | None = None,
) -> ReportEditorSpec:
    return ReportEditorSpec.model_validate(
        {
            "headline": "The measured pattern is visible",
            "summary": "The report uses verified metrics.",
            "sections": [
                {
                    "title": "Metric comparison",
                    "finding_refs": ["finding_visual"],
                    **({"section_role": section_role} if section_role else {}),
                    "blocks": [
                        {
                            "type": "chart",
                            "data_ref": "data/visual.csv",
                            "chart_type": "bar",
                            "x_field": "category_a",
                            "series": visual_fields or ["value_x", "value_y"],
                            "title": "Metric comparison",
                            "purpose": "Compare the measured metrics",
                        },
                        {
                            "type": "narrative",
                            "text": "The visual shows the measured relationship.",
                            "display_role": "evidence_interpretation",
                            "related_block_id": "data/visual.csv",
                            "related_block_ids": related_block_ids or [],
                            "metric_refs": metric_refs,
                            "claim_ids": claim_ids or [],
                        },
                    ],
                }
            ],
        }
    )


def test_editorial_context_exposes_visual_metric_metadata() -> None:
    context = EditorialContextBuilder.build(_inputs())

    visual = context["visuals"][0]
    assert visual["data_ref"] == "data/visual.csv"
    assert visual["metric_refs"] == ["metric_x", "metric_y"]
    assert visual["dimension"] == "category_a"
    assert visual["series"][0]["field_ref"] == "value_x"
    assert visual["series"][0]["metric_ref"] == "metric_x"
    assert visual["series"][0]["aggregation"] == "sum"
    assert visual["series"][0]["display_label"] == "Metric X"


def test_interpretation_metric_refs_match_visual_metrics() -> None:
    result = ReportSpecValidator.validate(_draft(["metric_x", "metric_y"]), _inputs())

    assert result.spec.sections
    assert not any(issue.code == "interpretation.metric_mismatch" for issue in result.issues)


def test_interpretation_cannot_replace_visual_metric_without_support() -> None:
    result = ReportSpecValidator.validate(
        _draft(["metric_y"], visual_fields=["value_x"]),
        _inputs(),
    )

    assert any(issue.code == "interpretation.metric_mismatch" for issue in result.issues)
    narratives = [block for block in result.spec.sections[0].blocks if block.type == "narrative"]
    assert narratives == []


def test_interpretation_may_add_explicitly_supported_metric() -> None:
    result = ReportSpecValidator.validate(
        _draft(
            ["metric_x", "metric_y"],
            visual_fields=["value_x"],
            claim_ids=["claim_visual"],
        ),
        _inputs(claim_metrics=["metric_x", "metric_y"]),
    )

    assert not any(issue.code == "interpretation.metric_mismatch" for issue in result.issues)
    narrative = next(block for block in result.spec.sections[0].blocks if block.type == "narrative")
    assert narrative.metric_refs == ["metric_x", "metric_y"]


def test_interpretation_rejects_cross_grain_supporting_scalar() -> None:
    inputs = _inputs(claim_metrics=["metric_x", "metric_y"])
    visual_metric = inputs.metrics[0].model_copy(update={"grain": "payment_record"})
    raw_scalar = inputs.metrics[1].model_copy(
        update={
            "metric_scope": "scalar_evidence",
            "source_artifact": "input/raw.csv",
            "grain": "mixed_item_payment_row",
        }
    )
    inputs = replace(inputs, metrics=[visual_metric, raw_scalar])

    result = ReportSpecValidator.validate(
        _draft(
            ["metric_x", "metric_y"],
            visual_fields=["value_x"],
            claim_ids=["claim_visual"],
        ),
        inputs,
    )

    assert any(
        issue.code == "INTERPRETATION_SUPPORTING_EVIDENCE_GRAIN_MISMATCH" for issue in result.issues
    )
    narratives = [block for block in result.spec.sections[0].blocks if block.type == "narrative"]
    assert narratives == []


def test_interpretation_can_explain_multiple_visual_series() -> None:
    result = ReportSpecValidator.validate(
        _draft(["metric_x", "metric_y"]),
        _inputs(),
    )

    assert not any(issue.code == "interpretation.metric_mismatch" for issue in result.issues)


def test_group_interpretation_preserves_split_visual_metrics() -> None:
    inputs = _inputs(claim_metrics=["metric_x", "metric_y"])
    draft = _draft(
        ["metric_x", "metric_y"],
        visual_fields=["value_x"],
        claim_ids=["claim_visual"],
        related_block_ids=["data/visual.csv"],
    )

    result = ReportSpecValidator.validate(draft, inputs)

    assert result.spec.sections
    narrative = next(block for block in result.spec.sections[0].blocks if block.type == "narrative")
    assert narrative.related_block_ids == ["data/visual.csv"]
    assert narrative.metric_refs == ["metric_x", "metric_y"]
    assert not any(issue.code == "interpretation.metric_mismatch" for issue in result.issues)


def test_group_interpretation_rejects_unproven_metric() -> None:
    result = ReportSpecValidator.validate(
        _draft(
            ["metric_x", "metric_y"],
            visual_fields=["value_x"],
            related_block_ids=["data/visual.csv"],
        ),
        _inputs(),
    )

    assert any(issue.code == "interpretation.metric_mismatch" for issue in result.issues)


def test_chart_led_section_requires_one_group_interpretation() -> None:
    draft = ReportEditorSpec.model_validate(
        {
            "headline": "The measured pattern is visible",
            "summary": "The report uses verified metrics.",
            "sections": [
                {
                    "title": "Metric comparison",
                    "finding_refs": ["finding_visual"],
                    "section_role": "chart_led",
                    "blocks": [
                        {
                            "type": "visual_group",
                            "layout": "two-column",
                            "items": [
                                {
                                    "type": "chart",
                                    "data_ref": "data/visual.csv",
                                    "chart_type": "bar",
                                    "x_field": "category_a",
                                    "series": ["value_x"],
                                    "title": "Metric X",
                                    "purpose": "Show Metric X",
                                },
                                {
                                    "type": "chart",
                                    "data_ref": "data/visual.csv",
                                    "chart_type": "bar",
                                    "x_field": "category_a",
                                    "series": ["value_y"],
                                    "title": "Metric Y",
                                    "purpose": "Show Metric Y",
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    )
    result = ReportSpecValidator.validate(draft, _inputs())
    assert any(issue.code == "interpretation.missing" for issue in result.issues)


def test_chart_led_visual_group_interpretation_satisfies_requirement() -> None:
    result = ReportSpecValidator.validate(
        _draft(["metric_x", "metric_y"], section_role="chart_led"), _inputs()
    )
    assert not any(issue.code == "interpretation.missing" for issue in result.issues)


def test_fraction_rate_reaches_final_chart_spec_and_renderer() -> None:
    inputs = _inputs()
    late = MetricDefinition(
        metric_id="late_rate",
        label="Late delivery rate",
        value=0.078466,
        aggregation="ratio",
        semantic_type="rate",
        unit_family="percentage",
        ratio_basis="fraction",
        numerator="late_orders",
        denominator="orders",
        numerator_value=7.8466,
        denominator_value=100,
        unit="%",
        definition="late_orders / orders",
        source_artifact="data/visual.csv",
    )
    manifest_payload = inputs.evidence_manifest.model_dump()
    manifest_payload["artifacts"][0]["chart"]["series"] = [
        {"field": "late_rate", "label": "Late delivery rate", "metric": "late_rate"}
    ]
    catalog_payload = inputs.catalog[0].structure.copy()
    catalog_payload["columns"] = [
        {"name": "category_a", "display_label": "Category A"},
        {
            "name": "late_rate",
            "display_label": "Late delivery rate",
            "type": "number",
            "semantic_type": "percentage_fraction",
        },
    ]
    prepared = replace(
        inputs,
        metrics=[late],
        evidence_manifest=ReportEvidenceManifest.model_validate(manifest_payload),
        catalog=[
            inputs.catalog[0].__class__(
                **{**inputs.catalog[0].__dict__, "structure": catalog_payload}
            )
        ],
    )
    draft = _draft(["late_rate"], visual_fields=["late_rate"], section_role="chart_led")
    assembled = ReportEditorAssembler().assemble(draft, prepared)
    assembled = PresentationMetadataResolver.apply(assembled, {"late_rate": late})
    series = assembled.sections[0].blocks[0].chart.series[0]
    assert series.format == "percent"
    assert series.decimals == 2
    assert series.scale == 0.01
    assert series.unit is None
    assert ReportRenderer._format(0.078466, series.format, series.decimals, series.scale) == "7.85%"


def test_analytical_chart_without_metric_definition_is_rejected() -> None:
    inputs = replace(_inputs(), metrics=[])
    inputs.catalog[0].structure["columns"][1]["type"] = "number"
    inputs.catalog[0].structure["columns"][2]["type"] = "number"
    draft = _draft(["metric_x", "metric_y"], section_role="chart_led")
    result = ReportSpecValidator.validate(draft, inputs)
    assert any(issue.code == "analytical_metric_definition_missing" for issue in result.issues)


def test_assembled_chart_led_without_interpretation_is_hard_error() -> None:
    inputs = _inputs()
    draft = _draft(["metric_x", "metric_y"], section_role="chart_led")
    draft = draft.model_copy(
        update={
            "sections": [
                draft.sections[0].model_copy(update={"blocks": [draft.sections[0].blocks[0]]})
            ]
        }
    )
    result = ReportSpecValidator.validate(draft, inputs)
    assembled = ReportEditorAssembler().assemble(result.spec, inputs)
    with pytest.raises(ReportPipelineError, match="evidence interpretation"):
        ReportSpecValidator.validate_assembled(assembled, inputs)
