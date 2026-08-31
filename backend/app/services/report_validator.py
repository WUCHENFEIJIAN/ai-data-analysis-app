"""Deterministic Report Editor checks. Illegal blocks are dropped or rejected."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.errors import ReportPipelineError
from app.services.report_editor_spec import (
    ReportEditorBlock,
    ReportEditorChartBlock,
    ReportEditorKpiGridBlock,
    ReportEditorNarrativeBlock,
    ReportEditorRecommendationsBlock,
    ReportEditorSpec,
    ReportEditorTableBlock,
    ReportEditorVisualGroupBlock,
)
from app.services.report_inputs import ArtifactEntry, ReportInputs
from app.services.report_limitation_attach import attach_report_limitations
from app.services.report_metric_fidelity import (
    eligible_visual_context_keys,
    metric_definition_for_field,
    visual_metric_refs,
)
from app.services.report_reportability import classify_claim
from app.services.report_semantics import narratives_are_duplicate


@dataclass
class ValidationIssue:
    code: str
    message: str
    target: str
    repair: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "target": self.target,
            "repair": self.repair,
        }


@dataclass
class ValidationResult:
    spec: ReportEditorSpec
    issues: list[ValidationIssue] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _analytical_role_for_blocks(blocks: list[Any], *, assembled: bool) -> str | None:
    """Infer the existing editorial role from analytical block presence only."""

    if assembled:
        from app.services.report_spec import ChartBlock, KpiGridBlock, TableBlock, VisualGroupBlock

        chart_type = ChartBlock
        table_type = TableBlock
        kpi_type = KpiGridBlock
        group_type = VisualGroupBlock
    else:
        chart_type = ReportEditorChartBlock
        table_type = ReportEditorTableBlock
        kpi_type = ReportEditorKpiGridBlock
        group_type = ReportEditorVisualGroupBlock

    has_chart = False
    has_table = False
    has_kpi = False
    for block in blocks:
        if isinstance(block, chart_type):
            if not assembled or block.chart.visual_purpose == "analytical":
                has_chart = True
        elif isinstance(block, table_type):
            has_table = True
        elif isinstance(block, kpi_type):
            has_kpi = True
        elif isinstance(block, group_type):
            nested = _analytical_role_for_blocks(block.items, assembled=assembled)
            has_chart = has_chart or nested == "chart_led"
            has_table = has_table or nested == "table_led"

    if has_chart:
        return "chart_led"
    if has_table:
        return "table_led"
    if has_kpi:
        return "kpi_led"
    return None


def _context_only_role_issue(
    section: Any,
    blocks: list[Any],
    section_index: int,
    *,
    assembled: bool,
    raise_on_conflict: bool = False,
) -> ValidationIssue | None:
    role_field = "visual_strategy" if assembled else "section_role"
    if getattr(section, role_field, None) != "context_only":
        return None
    expected = _analytical_role_for_blocks(blocks, assembled=assembled)
    if expected is None:
        return None
    if raise_on_conflict:
        raise ReportPipelineError(
            "report_editor_invalid_output",
            "context_only section contains an analytical visual",
            details={
                "section_id": getattr(section, "id", f"section_{section_index + 1}"),
                "issue": "section.role_visual_conflict",
                "normalized_role": expected,
            },
        )
    return ValidationIssue(
        "section.role_visual_conflict",
        f"context_only section contains analytical content; use {expected}",
        f"sections[{section_index}].section_role",
        (
            f"Change section_role to {expected}. Preserve all existing blocks, findings, "
            "claims, and visual selections."
        ),
    )


class ReportSpecValidator:
    """Keep only references that exist. Never invent KPI values or chart fields."""

    @classmethod
    def validate(cls, spec: ReportEditorSpec, inputs: ReportInputs) -> ValidationResult:
        metrics = {item.metric_id: item for item in inputs.metrics}
        artifacts = cls._artifact_index(inputs.catalog)
        finding_ids = {item.id for item in inputs.findings.findings}
        issues: list[ValidationIssue] = []
        dropped: list[str] = []

        kept_kpis = []
        for index, kpi in enumerate(spec.kpis):
            if kpi.metric_ref not in metrics:
                issues.append(
                    ValidationIssue(
                        "kpi.unknown_metric",
                        f"KPI references unknown metric: {kpi.metric_ref}",
                        f"kpis[{index}].metric_ref",
                        "Use an existing metric_ref or omit the KPI.",
                    )
                )
                dropped.append(f"kpi:{kpi.metric_ref}")
                continue
            kept_kpis.append(kpi)

        kept_sections = []
        for section_index, section in enumerate(spec.sections):
            unknown_findings = [item for item in section.finding_refs if item not in finding_ids]
            if unknown_findings:
                issues.append(
                    ValidationIssue(
                        "section.unknown_finding",
                        f"Section references unknown findings: {unknown_findings}",
                        f"sections[{section_index}].finding_refs",
                        "Use existing finding ids or omit the refs.",
                    )
                )
            kept_blocks: list[ReportEditorBlock] = []
            previous_narrative = None
            previous_claim_ids: list[str] | None = None
            for block_index, block in enumerate(section.blocks):
                location = f"sections[{section_index}].blocks[{block_index}]"
                if isinstance(block, ReportEditorNarrativeBlock):
                    if narratives_are_duplicate(previous_narrative, block.text):
                        issues.append(
                            ValidationIssue(
                                "section.duplicate_narrative",
                                "Consecutive narratives repeat the same conclusion",
                                location,
                                "Merge consecutive duplicate narratives into one block.",
                            )
                        )
                    previous_narrative = block.text
                    unique_claims = list(dict.fromkeys(block.claim_ids))
                    if len(unique_claims) != len(block.claim_ids):
                        issues.append(
                            ValidationIssue(
                                "block.duplicate_claim_ids",
                                "Narrative repeats the same claim id",
                                location,
                                "Bind each claim id once in this block.",
                            )
                        )
                    if unique_claims and unique_claims == previous_claim_ids:
                        issues.append(
                            ValidationIssue(
                                "block.repeated_claim_binding",
                                "Consecutive blocks repeat the same claim ids",
                                location,
                                "Keep one block for this claim instead of repeating it.",
                            )
                        )
                    previous_claim_ids = unique_claims
                if isinstance(block, ReportEditorChartBlock):
                    block_issues = cls._chart_issues(block, artifacts, metrics, inputs, location)
                    if block_issues:
                        issues.extend(block_issues)
                        dropped.append(f"chart:{block.data_ref}")
                        continue
                elif isinstance(block, ReportEditorTableBlock):
                    block_issues = cls._table_issues(block, artifacts, metrics, inputs, location)
                    if block_issues:
                        issues.extend(block_issues)
                        dropped.append(f"table:{block.data_ref}")
                        continue
                elif isinstance(block, ReportEditorVisualGroupBlock):
                    kept_items = []
                    group_failed = False
                    for item_index, item in enumerate(block.items):
                        item_location = f"{location}.items[{item_index}]"
                        if isinstance(item, ReportEditorChartBlock):
                            item_issues = cls._chart_issues(
                                item, artifacts, metrics, inputs, item_location
                            )
                            if item_issues:
                                issues.extend(item_issues)
                                dropped.append(f"chart:{item.data_ref}")
                                group_failed = True
                                continue
                        elif isinstance(item, ReportEditorTableBlock):
                            item_issues = cls._table_issues(
                                item, artifacts, metrics, inputs, item_location
                            )
                            if item_issues:
                                issues.extend(item_issues)
                                dropped.append(f"table:{item.data_ref}")
                                group_failed = True
                                continue
                        kept_items.append(item)
                    if group_failed or not kept_items:
                        continue
                    block = block.model_copy(update={"items": kept_items})
                elif isinstance(block, ReportEditorKpiGridBlock):
                    valid_refs = [ref for ref in block.metric_refs if ref in metrics]
                    for ref in set(block.metric_refs) - set(valid_refs):
                        issues.append(
                            ValidationIssue(
                                "kpi.unknown_metric",
                                f"KPI block references unknown metric: {ref}",
                                f"{location}.metric_refs",
                                "Use an existing metric_ref or remove it from the block.",
                            )
                        )
                        dropped.append(f"kpi-block:{ref}")
                    if not valid_refs:
                        continue
                    block = block.model_copy(update={"metric_refs": valid_refs})
                kept_blocks.append(block)
            finding_refs = [item for item in section.finding_refs if item in finding_ids]
            role_issue = _context_only_role_issue(
                section, kept_blocks, section_index, assembled=False
            )
            if role_issue is not None:
                issues.append(role_issue)
            interpretation_issues = cls._interpretation_issues(section, kept_blocks, section_index)
            issues.extend(interpretation_issues)
            kept_sections.append(
                section.model_copy(
                    update={
                        "finding_refs": finding_refs,
                        "blocks": kept_blocks,
                    }
                )
            )

        claim_index = cls._claim_index(inputs)
        artifact_paths = {entry.path for entry in inputs.catalog}
        referenced_sections = []
        for section_index, section in enumerate(kept_sections):
            cleaned, ref_issues, ref_dropped = cls._clean_section_references(
                section,
                section_index,
                claim_index,
                artifact_paths,
                set(metrics),
                finding_ids,
                inputs,
            )
            issues.extend(ref_issues)
            dropped.extend(ref_dropped)
            if cleaned is not None and cleaned.blocks:
                referenced_sections.append(cleaned)
        kept_sections = referenced_sections

        signatures = [tuple(item.type for item in section.blocks) for section in kept_sections]
        for index in range(len(signatures) - 2):
            if (
                signatures[index]
                and signatures[index] == signatures[index + 1] == signatures[index + 2]
                and len(signatures[index]) >= 2
            ):
                issues.append(
                    ValidationIssue(
                        "section.repeated_structure",
                        "Three consecutive sections use the same block order",
                        f"sections[{index}]",
                        "Vary section structure when the topics are different.",
                    )
                )
                break

        cleaned = spec.model_copy(update={"kpis": kept_kpis, "sections": kept_sections})
        cleaned = attach_report_limitations(cleaned, inputs)
        if not cleaned.sections:
            issues.append(
                ValidationIssue(
                    "report.empty",
                    "Report has no remaining sections",
                    "sections",
                    "Keep at least one narrative section from existing findings.",
                )
            )
        return ValidationResult(spec=cleaned, issues=issues, dropped=dropped)

    @staticmethod
    def _visual_data_refs(section: Any) -> set[str]:
        refs: set[str] = set()
        for block in section.blocks:
            if isinstance(block, (ReportEditorChartBlock, ReportEditorTableBlock)):
                refs.add(block.data_ref)
            elif isinstance(block, ReportEditorVisualGroupBlock):
                refs.update(
                    item.data_ref
                    for item in block.items
                    if isinstance(item, (ReportEditorChartBlock, ReportEditorTableBlock))
                )
        return refs

    @classmethod
    def _interpretation_issues(
        cls, section: Any, blocks: list[ReportEditorBlock], section_index: int
    ) -> list[ValidationIssue]:
        """Require one So-What narrative per core analytical visual group."""

        groups = cls._interpretation_groups(section, blocks, assembled=False)
        if not groups:
            return []
        interpretations = [
            block
            for block in blocks
            if isinstance(block, ReportEditorNarrativeBlock)
            and block.display_role == "evidence_interpretation"
        ]
        related = [
            {item for item in [block.related_block_id, *block.related_block_ids] if item}
            for block in interpretations
        ]
        issues: list[ValidationIssue] = []
        for refs in groups:
            if any(refs & item for item in related):
                continue
            issues.append(
                ValidationIssue(
                    "interpretation.missing",
                    "Analytical visual group has no evidence interpretation",
                    f"sections[{section_index}].blocks",
                    (
                        "Add one evidence_interpretation after the visual group, reference "
                        "its chart/table data_ref values, and explain the main relationship "
                        "or meaning. Do not create one paragraph per chart."
                    ),
                )
            )
        return issues

    @staticmethod
    def _interpretation_groups(
        section: Any, blocks: list[Any], *, assembled: bool
    ) -> list[set[str]]:
        """Return core analytical visual groups for draft or assembled sections."""
        from app.services.report_spec import ChartBlock, TableBlock, VisualGroupBlock

        visual_group_type = VisualGroupBlock if assembled else ReportEditorVisualGroupBlock
        chart_types = (
            (ChartBlock, TableBlock)
            if assembled
            else (
                ReportEditorChartBlock,
                ReportEditorTableBlock,
            )
        )
        role = getattr(section, "visual_strategy" if assembled else "section_role", None)
        if role not in {"chart_led", "table_led"} and not any(
            isinstance(block, visual_group_type) for block in blocks
        ):
            return []

        def visual_ref(block: Any) -> str:
            if not assembled:
                return block.data_ref
            return block.chart.source_id if isinstance(block, ChartBlock) else block.source_id

        groups: list[set[str]] = []
        standalone: set[str] = set()
        for block in blocks:
            if isinstance(block, chart_types):
                if (
                    assembled
                    and isinstance(block, ChartBlock)
                    and block.chart.visual_purpose != "analytical"
                ):
                    continue
                standalone.add(visual_ref(block))
                continue
            if isinstance(block, visual_group_type):
                if standalone:
                    groups.append(standalone)
                    standalone = set()
                refs: set[str] = set()
                for item in block.items:
                    if isinstance(item, chart_types):
                        if (
                            assembled
                            and isinstance(item, ChartBlock)
                            and item.chart.visual_purpose != "analytical"
                        ):
                            continue
                        refs.add(visual_ref(item))
                if refs:
                    groups.append(refs)
                continue
            if standalone:
                groups.append(standalone)
                standalone = set()
        if standalone:
            groups.append(standalone)
        return groups

    @staticmethod
    def _recommendation_has_reportable_source(
        finding_ids: list[str],
        claim_ids: list[str],
        claim_index: dict[str, Any],
    ) -> bool:
        roles_by_finding: dict[str, set[str]] = {}
        for info in claim_index.values():
            roles_by_finding.setdefault(info["finding_id"], set()).add(
                getattr(info["claim"], "report_role", "business_insight")
            )
        reportable = {"business_insight", "report_limitation"}
        if any(
            getattr(claim_index[claim_id]["claim"], "report_role", "business_insight") in reportable
            for claim_id in claim_ids
            if claim_id in claim_index
        ):
            return True
        for finding_id in finding_ids:
            roles = roles_by_finding.get(finding_id)
            if roles is None or roles & reportable:
                return True
        return False

    @staticmethod
    def _claim_index(inputs: ReportInputs) -> dict[str, Any]:
        index: dict[str, Any] = {}
        for finding in inputs.findings.findings:
            for claim in finding.claims:
                classified = claim.model_copy(update={"report_role": classify_claim(claim)})
                index[claim.claim_id] = {"finding_id": finding.id, "claim": classified}
        return index

    @classmethod
    def _clean_section_references(
        cls,
        section,
        section_index: int,
        claim_index: dict[str, Any],
        artifact_paths: set[str],
        metric_ids: set[str],
        finding_ids: set[str],
        inputs: ReportInputs,
    ) -> tuple[Any, list[ValidationIssue], list[str]]:
        issues: list[ValidationIssue] = []
        dropped: list[str] = []
        prefix = f"sections[{section_index}]"
        section_findings = set(section.finding_refs)

        def resolve_claims(claim_ids: list[str], location: str) -> list[str]:
            kept: list[str] = []
            for claim_id in claim_ids:
                info = claim_index.get(claim_id)
                if info is None:
                    issues.append(
                        ValidationIssue(
                            "claim.unknown",
                            f"Unknown claim id: {claim_id}",
                            location,
                            "Use an existing claim_id from the input or omit it.",
                        )
                    )
                    dropped.append(f"claim:{claim_id}")
                    continue
                if section_findings and info["finding_id"] not in section_findings:
                    issues.append(
                        ValidationIssue(
                            "claim.wrong_finding",
                            f"Claim {claim_id} does not belong to this section's findings",
                            location,
                            "Bind claims that belong to the section finding_refs.",
                        )
                    )
                    dropped.append(f"claim:{claim_id}")
                    continue
                claim = info["claim"]
                missing_artifacts = [
                    path for path in claim.evidence_artifact_paths if path not in artifact_paths
                ]
                missing_metrics = [
                    metric for metric in claim.evidence_metric_ids if metric not in metric_ids
                ]
                if missing_artifacts or missing_metrics:
                    issues.append(
                        ValidationIssue(
                            "claim.missing_evidence",
                            f"Claim {claim_id} is missing required evidence",
                            location,
                            "Bind a claim whose artifacts and metrics exist, or omit it.",
                        )
                    )
                    dropped.append(f"claim:{claim_id}")
                    continue
                kept.append(claim_id)
            return list(dict.fromkeys(kept))

        section_claims = resolve_claims(section.claim_ids, f"{prefix}.claim_ids")
        kept_blocks: list[Any] = []
        for block_index, block in enumerate(section.blocks):
            location = f"{prefix}.blocks[{block_index}]"
            if isinstance(block, ReportEditorNarrativeBlock):
                if block.composite_insight_ids:
                    issues.append(
                        ValidationIssue(
                            "block.composite_insight_not_supported",
                            "Composite insight ids are not assembled in this version",
                            f"{location}.composite_insight_ids",
                            "Remove composite_insight_ids; use existing claim_ids instead.",
                        )
                    )
                    dropped.extend(f"insight:{item}" for item in block.composite_insight_ids)
                block = block.model_copy(
                    update={
                        "claim_ids": resolve_claims(block.claim_ids, f"{location}.claim_ids"),
                        "composite_insight_ids": [],
                    }
                )
                if block.display_role == "evidence_interpretation":
                    related_ids = list(
                        dict.fromkeys(
                            [
                                item
                                for item in [block.related_block_id, *block.related_block_ids]
                                if item
                            ]
                        )
                    )
                    visual_data_refs = cls._visual_data_refs(section)
                    related_visual_ids = [item for item in related_ids if item in visual_data_refs]
                    visual_refs = {
                        metric_id
                        for related_id in related_visual_ids
                        for metric_id in visual_metric_refs(section, related_id, inputs)
                    }
                    allowed_supporting = {
                        metric_id
                        for claim_id in [*section.claim_ids, *block.claim_ids]
                        if (info := claim_index.get(claim_id)) is not None
                        for metric_id in info["claim"].evidence_metric_ids
                    }
                    metric_registry = {item.metric_id: item for item in inputs.metrics}
                    visual_grains = {
                        metric.grain
                        for metric_id in visual_refs
                        if (metric := metric_registry.get(metric_id)) is not None
                        and metric.grain is not None
                    }
                    incompatible_supporting = []
                    for metric_id in block.metric_refs:
                        if metric_id in visual_refs or metric_id not in allowed_supporting:
                            continue
                        metric = metric_registry.get(metric_id)
                        if metric is None:
                            continue
                        same_artifact = metric.source_artifact in related_visual_ids
                        same_grain = (
                            not visual_grains
                            or metric.grain is None
                            or metric.grain in visual_grains
                        )
                        if not same_artifact or not same_grain:
                            incompatible_supporting.append(metric_id)
                    unknown_metric_refs = [
                        metric_id for metric_id in block.metric_refs if metric_id not in metric_ids
                    ]
                    unsupported_metric_refs = [
                        metric_id
                        for metric_id in block.metric_refs
                        if metric_id not in visual_refs and metric_id not in allowed_supporting
                    ]
                    if not related_visual_ids:
                        issues.append(
                            ValidationIssue(
                                "interpretation.related_visual_missing",
                                "Evidence interpretation must reference a chart or "
                                "table in this section",
                                f"{location}.related_block_id",
                                "Use related visual data_ref values or omit the interpretation.",
                            )
                        )
                        dropped.append(f"narrative:{location}")
                        continue
                    if unknown_metric_refs:
                        issues.append(
                            ValidationIssue(
                                "narrative.unknown_metric",
                                f"Narrative references unknown metrics: {unknown_metric_refs}",
                                f"{location}.metric_refs",
                                "Use existing metric refs or omit them.",
                            )
                        )
                    if unsupported_metric_refs:
                        issues.append(
                            ValidationIssue(
                                "interpretation.metric_mismatch",
                                (
                                    "Evidence interpretation references metrics not shown by "
                                    f"the related visual or its supporting claims: "
                                    f"{unsupported_metric_refs}"
                                ),
                                f"{location}.metric_refs",
                                (
                                    "Keep the visual metrics, or bind an existing supporting "
                                    "claim metric explicitly."
                                ),
                            )
                        )
                        dropped.append(f"narrative:{location}")
                        continue
                    if incompatible_supporting:
                        issues.append(
                            ValidationIssue(
                                "INTERPRETATION_SUPPORTING_EVIDENCE_GRAIN_MISMATCH",
                                (
                                    "Quantitative supporting metrics do not share the related "
                                    f"visual artifact/grain: {incompatible_supporting}"
                                ),
                                f"{location}.metric_refs",
                                (
                                    "Use supporting scalar evidence reproduced from the related "
                                    "visual artifact/grain, or omit it from the interpretation."
                                ),
                            )
                        )
                        dropped.append(f"narrative:{location}")
                        continue
                    block = block.model_copy(
                        update={
                            "related_block_id": related_visual_ids[0],
                            "related_block_ids": related_visual_ids,
                            "metric_refs": [
                                metric_id
                                for metric_id in block.metric_refs
                                if metric_id in metric_ids
                            ],
                        }
                    )
            elif isinstance(block, ReportEditorRecommendationsBlock):
                items = []
                for item_index, item in enumerate(block.items):
                    item_location = f"{location}.items[{item_index}]"
                    kept_claims = resolve_claims(
                        item.source_claim_ids, f"{item_location}.source_claim_ids"
                    )
                    kept_findings = [
                        item_id for item_id in item.source_finding_ids if item_id in finding_ids
                    ]
                    unknown_findings = [
                        item_id for item_id in item.source_finding_ids if item_id not in finding_ids
                    ]
                    if unknown_findings:
                        issues.append(
                            ValidationIssue(
                                "recommendation.unknown_finding",
                                f"Recommendation references unknown findings: {unknown_findings}",
                                f"{item_location}.source_finding_ids",
                                "Use existing finding ids or omit them.",
                            )
                        )
                    if not kept_findings and not kept_claims:
                        issues.append(
                            ValidationIssue(
                                "recommendation.missing_source",
                                "Recommendation has no remaining finding or claim source",
                                item_location,
                                "Bind an existing finding or claim, or omit the recommendation.",
                            )
                        )
                        dropped.append(f"recommendation:{item_index}")
                        continue
                    if not cls._recommendation_has_reportable_source(
                        kept_findings, kept_claims, claim_index
                    ):
                        issues.append(
                            ValidationIssue(
                                "recommendation.internal_diagnostic",
                                "Recommendation is sourced only from internal diagnostics",
                                item_location,
                                "Omit recommendations derived only from internal quality checks.",
                            )
                        )
                        dropped.append(f"recommendation:{item_index}")
                        continue
                    items.append(
                        item.model_copy(
                            update={
                                "source_finding_ids": kept_findings,
                                "source_claim_ids": kept_claims,
                            }
                        )
                    )
                if not items:
                    dropped.append("recommendations")
                    continue
                block = block.model_copy(update={"items": items})
            kept_blocks.append(block)
        if not kept_blocks:
            return None, issues, dropped
        return (
            section.model_copy(update={"claim_ids": section_claims, "blocks": kept_blocks}),
            issues,
            dropped,
        )

    @classmethod
    def validate_assembled(cls, spec: Any, inputs: ReportInputs) -> None:
        """Reject dangling claim or composite insight references before render."""

        claim_ids = {
            claim.claim_id for finding in inputs.findings.findings for claim in finding.claims
        }
        composites = {
            item.id for item in (spec.storyline.composite_insights if spec.storyline else [])
        }
        finding_ids = {item.id for item in inputs.findings.findings}
        for section in spec.sections:
            _context_only_role_issue(
                section, section.blocks, 0, assembled=True, raise_on_conflict=True
            )
            groups = cls._interpretation_groups(section, section.blocks, assembled=True)
            if groups:
                from app.services.report_spec import NarrativeBlock

                has_interpretation = any(
                    isinstance(block, NarrativeBlock)
                    and block.display_role == "evidence_interpretation"
                    for block in section.blocks
                )
                if not has_interpretation:
                    raise ReportPipelineError(
                        "report_editor_invalid_output",
                        "Core analytical visual group has no evidence interpretation",
                        details={"section_id": section.id, "issue": "interpretation.missing"},
                    )
            cls._assert_refs(
                section.claim_ids,
                claim_ids,
                section_id=section.id,
                block_type="section",
                reference_type="claim_id",
            )
            for block in section.blocks:
                cls._assert_assembled_block(block, section.id, claim_ids, composites, finding_ids)

    @classmethod
    def _assert_assembled_block(
        cls,
        block: Any,
        section_id: str,
        claim_ids: set[str],
        composites: set[str],
        finding_ids: set[str],
    ) -> None:
        from app.services.report_spec import (
            ChartBlock,
            NarrativeBlock,
            RecommendationBlock,
            TableBlock,
            VisualGroupBlock,
        )

        if isinstance(block, NarrativeBlock):
            cls._assert_refs(
                block.claim_ids,
                claim_ids,
                section_id=section_id,
                block_type="narrative",
                reference_type="claim_id",
            )
            cls._assert_refs(
                block.composite_insight_ids,
                composites,
                section_id=section_id,
                block_type="narrative",
                reference_type="composite_insight_id",
            )
        elif isinstance(block, RecommendationBlock):
            for item in block.items:
                cls._assert_refs(
                    item.source_claim_ids,
                    claim_ids,
                    section_id=section_id,
                    block_type="recommendations",
                    reference_type="claim_id",
                )
                cls._assert_refs(
                    item.source_finding_ids,
                    finding_ids,
                    section_id=section_id,
                    block_type="recommendations",
                    reference_type="finding_id",
                )
        elif isinstance(block, ChartBlock):
            cls._assert_refs(
                block.chart.supports_claim_ids,
                claim_ids,
                section_id=section_id,
                block_type="chart",
                reference_type="claim_id",
            )
        elif isinstance(block, TableBlock):
            cls._assert_refs(
                block.supports_claim_ids,
                claim_ids,
                section_id=section_id,
                block_type="table",
                reference_type="claim_id",
            )
        elif isinstance(block, VisualGroupBlock):
            for item in block.items:
                cls._assert_assembled_block(item, section_id, claim_ids, composites, finding_ids)

    @staticmethod
    def _assert_refs(
        values: list[str],
        allowed: set[str],
        *,
        section_id: str,
        block_type: str,
        reference_type: str,
    ) -> None:
        for value in values:
            if value not in allowed:
                raise ReportPipelineError(
                    "report_reference_invalid",
                    details={
                        "section": section_id,
                        "block": block_type,
                        "reference_type": reference_type,
                        "reference_id": value,
                    },
                )

    @classmethod
    def _chart_issues(
        cls,
        visual: ReportEditorChartBlock,
        artifacts: dict[str, ArtifactEntry],
        metrics: dict[str, Any],
        inputs: ReportInputs,
        location: str,
    ) -> list[ValidationIssue]:
        entry = artifacts.get(visual.data_ref)
        if entry is None:
            return [
                ValidationIssue(
                    "chart.unknown_artifact",
                    f"Chart data_ref does not exist: {visual.data_ref}",
                    f"{location}.data_ref",
                    "Use a catalog artifact path or omit the chart.",
                )
            ]
        if entry.kind not in {"csv", "json"}:
            return [
                ValidationIssue(
                    "chart.not_tabular",
                    f"Chart data_ref is not tabular: {visual.data_ref}",
                    f"{location}.data_ref",
                    "Use a CSV/JSON chart-ready artifact or omit the chart.",
                )
            ]
        if (entry.path, "chart") not in eligible_visual_context_keys(inputs):
            return [
                ValidationIssue(
                    "chart.not_report_ready",
                    f"Chart data_ref is not accepted report-ready evidence: {visual.data_ref}",
                    f"{location}.data_ref",
                    "Use an eligible report-ready chart context or omit the chart.",
                )
            ]
        fields = cls._fields(entry)
        required = [visual.x_field, *visual.series]
        missing = [name for name in required if name not in fields]
        if missing:
            return [
                ValidationIssue(
                    "chart.unknown_field",
                    f"Chart references unknown fields: {missing}",
                    f"{location}",
                    "Use fields from the artifact schema or omit the chart.",
                )
            ]
        x_binding = cls._field_binding(entry, visual.x_field)
        invalid_series = [
            field
            for field in visual.series
            if not cls._is_declared_measure(cls._field_binding(entry, field))
        ]
        if x_binding.get("role") != "dimension" or invalid_series:
            return [
                ValidationIssue(
                    "chart.field_not_report_ready",
                    (
                        "Chart fields are outside the accepted report-ready declaration: "
                        f"dimension={visual.x_field}, series={invalid_series}"
                    ),
                    location,
                    "Use the declared dimension and reusable measure fields.",
                )
            ]
        missing_metrics = [
            field
            for field in visual.series
            if metric_definition_for_field(entry.path, field, inputs) is None
            and cls._field_is_quantitative(entry, field)
        ]
        if missing_metrics:
            return [
                ValidationIssue(
                    "analytical_metric_definition_missing",
                    ("Analytical chart series require MetricDefinition: " f"{missing_metrics}"),
                    f"{location}.series",
                    "Use a registered metric or omit the quantitative series/chart.",
                )
            ]
        return []

    @classmethod
    def _table_issues(
        cls,
        table: ReportEditorTableBlock,
        artifacts: dict[str, ArtifactEntry],
        metrics: dict[str, Any],
        inputs: ReportInputs,
        location: str,
    ) -> list[ValidationIssue]:
        entry = artifacts.get(table.data_ref)
        if entry is None:
            return [
                ValidationIssue(
                    "table.unknown_artifact",
                    f"Table data_ref does not exist: {table.data_ref}",
                    f"{location}.data_ref",
                    "Use a catalog artifact path or omit the table.",
                )
            ]
        if cls._is_raw_input(entry):
            return [
                ValidationIssue(
                    "table.raw_input",
                    f"Raw input data cannot be shown as a report table: {table.data_ref}",
                    f"{location}.data_ref",
                    "Use a summary table artifact or omit the table.",
                )
            ]
        if (entry.path, "table") not in eligible_visual_context_keys(inputs):
            return [
                ValidationIssue(
                    "table.not_report_ready",
                    f"Table data_ref is not accepted report-ready evidence: {table.data_ref}",
                    f"{location}.data_ref",
                    "Use an eligible report-ready table context or omit the table.",
                )
            ]
        fields = cls._fields(entry)
        missing = [name for name in table.columns if name not in fields]
        if missing:
            return [
                ValidationIssue(
                    "table.unknown_field",
                    f"Table references unknown columns: {missing}",
                    location,
                    "Use columns from the artifact schema or omit the table.",
                )
            ]
        bindings = [cls._field_binding(entry, field) for field in table.columns]
        invalid = [
            field
            for field, binding in zip(table.columns, bindings, strict=True)
            if binding.get("role") != "dimension" and not cls._is_declared_measure(binding)
        ]
        if (
            invalid
            or not any(item.get("role") == "dimension" for item in bindings)
            or not any(cls._is_declared_measure(item) for item in bindings)
        ):
            return [
                ValidationIssue(
                    "table.field_not_report_ready",
                    f"Table columns are outside the accepted analytical declaration: {invalid}",
                    location,
                    "Use at least one declared dimension and one reusable measure field.",
                )
            ]
        return []

    @staticmethod
    def _field_binding(entry: ArtifactEntry, field: str) -> dict[str, Any]:
        return next(
            (
                column
                for column in (entry.structure or {}).get("columns", [])
                if column.get("name") == field
            ),
            {},
        )

    @staticmethod
    def _is_declared_measure(binding: dict[str, Any]) -> bool:
        return bool(
            binding.get("role") == "measure"
            and binding.get("metric_ref")
            and binding.get("presentation_usable", True)
        )

    @staticmethod
    def _field_is_quantitative(entry: ArtifactEntry, field: str) -> bool:
        for column in (entry.structure or {}).get("columns", []):
            if column.get("name") != field:
                continue
            semantic = str(column.get("semantic_type") or "").lower()
            field_type = str(column.get("type") or column.get("dtype") or "").lower()
            return semantic not in {"text", "identifier", "date", "datetime"} and (
                field_type in {"number", "integer", "float", "decimal"}
                or semantic
                in {
                    "integer",
                    "decimal",
                    "currency",
                    "percentage_fraction",
                    "percentage_points",
                }
            )
        return True

    @staticmethod
    def _artifact_index(catalog: list[ArtifactEntry]) -> dict[str, ArtifactEntry]:
        index: dict[str, ArtifactEntry] = {}
        for entry in catalog:
            index[entry.path] = entry
            index[entry.id] = entry
        return index

    @staticmethod
    def _fields(entry: ArtifactEntry) -> set[str]:
        structure = entry.structure or {}
        names = {item.get("name") for item in structure.get("columns", []) if item.get("name")}
        names.update(item.get("name") for item in structure.get("fields", []) if item.get("name"))
        return {name for name in names if isinstance(name, str)}

    @staticmethod
    def _is_raw_input(entry: ArtifactEntry) -> bool:
        return entry.path.startswith("input/")
