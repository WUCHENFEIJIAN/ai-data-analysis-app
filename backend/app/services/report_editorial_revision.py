"""One-shot local editorial revision. No repair loop."""

from __future__ import annotations

from app.services.report_editor_spec import (
    ReportEditorChartBlock,
    ReportEditorKpiGridBlock,
    ReportEditorRecommendationsBlock,
    ReportEditorRevision,
    ReportEditorSpec,
    ReportEditorTableBlock,
    ReportEditorVisualGroupBlock,
)
from app.services.report_editorial_lint import EditorialLintResult


def affected_sections(spec: ReportEditorSpec, lint: EditorialLintResult) -> list:
    indexes = sorted({item.section_index for item in lint.warnings})
    return [spec.sections[index] for index in indexes if 0 <= index < len(spec.sections)]


def merge_revision(spec: ReportEditorSpec, revision: ReportEditorRevision) -> ReportEditorSpec:
    by_title = {section.title: section for section in revision.sections}
    merged = []
    for section in spec.sections:
        replacement = by_title.get(section.title)
        if replacement is None:
            merged.append(section)
            continue
        if not replacement.finding_refs and section.finding_refs:
            replacement = replacement.model_copy(
                update={"finding_refs": list(section.finding_refs)}
            )
        merged.append(_preserve_visuals(section, replacement))
    return spec.model_copy(update={"sections": merged})


def _preserve_visuals(original, revised):
    original_visuals = [block for block in original.blocks if _visual_key(block)]
    revised_keys = {_visual_key(block) for block in revised.blocks if _visual_key(block)}
    missing = [block for block in original_visuals if _visual_key(block) not in revised_keys]
    if not missing:
        return revised
    blocks = list(revised.blocks)
    insert_at = next(
        (
            index
            for index, block in enumerate(blocks)
            if isinstance(block, ReportEditorRecommendationsBlock)
        ),
        len(blocks),
    )
    for block in missing:
        blocks.insert(insert_at, block)
        insert_at += 1
    return revised.model_copy(update={"blocks": blocks})


def _visual_key(block) -> tuple | None:
    if isinstance(block, (ReportEditorChartBlock, ReportEditorTableBlock)):
        return (block.type, block.data_ref)
    if isinstance(block, ReportEditorVisualGroupBlock):
        return (
            "visual_group",
            tuple((item.type, item.data_ref) for item in block.items),
        )
    if isinstance(block, ReportEditorKpiGridBlock):
        return ("kpi_grid", tuple(block.metric_refs))
    return None
