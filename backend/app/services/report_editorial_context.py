"""Compact Report Editor context. Server facts stay server-side."""

from __future__ import annotations

from typing import Any

from app.services.report_inputs import ReportInputs
from app.services.report_metric_fidelity import eligible_visual_contexts
from app.services.report_reportability import (
    apply_reportability,
    business_findings_for_report,
    limitation_items,
)


class EditorialContextBuilder:
    """Build the small, complete context consumed by the Report Editor."""

    @classmethod
    def build(cls, inputs: ReportInputs) -> dict[str, Any]:
        classified = apply_reportability(inputs.findings)
        business = business_findings_for_report(classified)
        eligible_visuals = eligible_visual_contexts(inputs)
        return {
            "user_request": inputs.user_request,
            "analysis_topic": inputs.analysis_topic,
            "analysis_plan": inputs.analysis_plan,
            "dataset_summary": inputs.dataset_profile,
            "findings": [
                {
                    "id": finding.id,
                    "title": finding.title,
                    "statement": finding.title,
                    "evidence": list(finding.evidence),
                    "risk": finding.risk,
                    "recommendation": finding.recommendation,
                    "claims": [cls._claim_item(finding.id, claim) for claim in finding.claims],
                }
                for finding in business
            ],
            "claims": [
                cls._claim_item(finding.id, claim)
                for finding in business
                for claim in finding.claims
            ],
            "report_limitations": limitation_items(classified),
            "metrics": [
                {
                    "metric_id": metric.metric_id,
                    "label": metric.label,
                    "display_label": metric.label,
                    "value": metric.value,
                    "unit": metric.unit,
                    "unit_family": metric.unit_family,
                    "semantic_type": metric.semantic_type,
                    "aggregation": metric.aggregation,
                    "count_semantics": metric.count_semantics,
                    "grain": metric.grain,
                    "is_distinct": metric.is_distinct,
                    "definition": metric.definition,
                }
                for metric in inputs.metrics
            ],
            "visuals": eligible_visuals,
            "eligible_visuals": eligible_visuals,
            "artifact_catalog": [
                cls._artifact_item(entry)
                for entry in inputs.catalog
                if not entry.path.startswith(("input/", "logs/", "reports/", "scripts/"))
            ],
            "constraints": {
                "report_spec_only": True,
                "do_not_recalculate": True,
                "omit_missing_materials": True,
                "charts_and_kpis_are_optional": not bool(eligible_visuals),
                "analytical_visual_required_when_eligible": bool(eligible_visuals),
                "raw_input_must_not_appear": True,
                "use_existing_claims_only": True,
                "do_not_output_composite_insight_ids": True,
                "internal_diagnostics_excluded": True,
                "limitations_are_not_business_findings": True,
                "preserve_field_semantics": True,
            },
        }

    @staticmethod
    def _claim_item(finding_id: str, claim: Any) -> dict[str, Any]:
        return {
            "claim_id": claim.claim_id,
            "finding_id": finding_id,
            "statement": claim.statement,
            "priority": claim.priority,
            "narrative_role": claim.narrative_role,
            "report_role": claim.report_role,
            "evidence_artifact_paths": list(claim.evidence_artifact_paths),
            "evidence_metric_ids": list(claim.evidence_metric_ids),
        }

    @staticmethod
    def _artifact_item(entry: Any) -> dict[str, Any]:
        structure = entry.structure or {}
        columns = [
            {
                "name": item.get("name"),
                "dtype": item.get("dtype") or item.get("type"),
                "semantic_type": item.get("semantic_type"),
                "display_label": item.get("display_label") or item.get("name"),
                "role": item.get("role"),
                "metric_ref": item.get("metric_ref"),
                "presentation_usable": item.get("presentation_usable", True),
            }
            for item in structure.get("columns", [])
            if item.get("name")
        ]
        return {
            "id": entry.id,
            "path": entry.path,
            "title": entry.path,
            "kind": entry.kind,
            "purpose": structure.get("record_kind", entry.kind),
            "report_ready": entry.report_ready,
            "columns": columns,
            "row_count": structure.get("row_count"),
        }
