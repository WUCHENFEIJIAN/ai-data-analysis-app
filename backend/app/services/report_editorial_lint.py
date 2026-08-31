"""Lightweight deterministic editorial lint. Warnings only; no NLP."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.report_editor_spec import (
    ReportEditorCalloutBlock,
    ReportEditorChartBlock,
    ReportEditorNarrativeBlock,
    ReportEditorRecommendationsBlock,
    ReportEditorSpec,
    ReportEditorTableBlock,
    ReportEditorVisualGroupBlock,
)

EXACT_PARAGRAPH_DUPLICATE = "EXACT_PARAGRAPH_DUPLICATE"
CONSECUTIVE_NARRATIVE_SAME_ROLE = "CONSECUTIVE_NARRATIVE_SAME_ROLE"
NARRATIVE_CLAIM_OVERLAP = "NARRATIVE_CLAIM_OVERLAP"
NARRATIVE_METRIC_OVERLAP = "NARRATIVE_METRIC_OVERLAP"
INTERPRETATION_WITHOUT_VISUAL = "INTERPRETATION_WITHOUT_VISUAL"
SECTION_LEAD_TOO_DENSE = "SECTION_LEAD_TOO_DENSE"
REPEATED_BLOCK_PATTERN = "REPEATED_BLOCK_PATTERN"

_PUNCTUATION = re.compile(r"[，。、；：,.!?;:\"'“”‘’（）()【】\[\]《》—\-]")
_WHITESPACE = re.compile(r"\s+")
_SENTENCE = re.compile(r"[。！？.!?]+")


@dataclass(frozen=True)
class EditorialWarning:
    code: str
    section_index: int
    section_title: str
    message: str
    block_index: int | None = None


@dataclass
class EditorialLintResult:
    warnings: list[EditorialWarning] = field(default_factory=list)

    def codes(self) -> list[str]:
        return [item.code for item in self.warnings]

    def should_revise(self) -> bool:
        if any(
            item.code
            in {
                CONSECUTIVE_NARRATIVE_SAME_ROLE,
                SECTION_LEAD_TOO_DENSE,
                REPEATED_BLOCK_PATTERN,
            }
            for item in self.warnings
        ):
            return True
        for item in self.warnings:
            if item.code != EXACT_PARAGRAPH_DUPLICATE:
                continue
            if "section.lead -> narrative." in item.message:
                continue
            return True
        claim_overlap = [item for item in self.warnings if item.code == NARRATIVE_CLAIM_OVERLAP]
        metric_overlap = [item for item in self.warnings if item.code == NARRATIVE_METRIC_OVERLAP]
        if claim_overlap and metric_overlap:
            return True
        return any("identical claim set" in item.message for item in claim_overlap)


class EditorialLint:
    @classmethod
    def check(cls, spec: ReportEditorSpec) -> EditorialLintResult:
        warnings: list[EditorialWarning] = []
        for index, section in enumerate(spec.sections):
            warnings.extend(cls._check_section(index, section))
        return EditorialLintResult(warnings=warnings)

    @classmethod
    def _check_section(cls, section_index: int, section) -> list[EditorialWarning]:
        warnings: list[EditorialWarning] = []
        title = section.title
        visual_refs = _visual_refs(section)
        narratives = [
            (block_index, block)
            for block_index, block in enumerate(section.blocks)
            if isinstance(block, ReportEditorNarrativeBlock)
        ]
        paragraphs: list[tuple[str, str, int | None]] = []
        if section.lead:
            for paragraph in _paragraphs(section.lead):
                paragraphs.append((paragraph, "section.lead", None))
            if _lead_is_dense(section.lead, []):
                warnings.append(
                    EditorialWarning(
                        code=SECTION_LEAD_TOO_DENSE,
                        section_index=section_index,
                        section_title=title,
                        message="Section lead lists too many facts instead of one judgment",
                    )
                )
        previous_role = None
        previous_claims: list[str] | None = None
        previous_metrics: list[str] | None = None
        for block_index, block in narratives:
            if block.display_role == "lead" and _lead_is_dense(block.text, block.claim_ids):
                warnings.append(
                    EditorialWarning(
                        code=SECTION_LEAD_TOO_DENSE,
                        section_index=section_index,
                        section_title=title,
                        message="Lead narrative lists too many facts instead of one judgment",
                        block_index=block_index,
                    )
                )
            for paragraph in _paragraphs(block.text):
                paragraphs.append((paragraph, f"narrative.{block.display_role}", block_index))
            if previous_role == block.display_role:
                warnings.append(
                    EditorialWarning(
                        code=CONSECUTIVE_NARRATIVE_SAME_ROLE,
                        section_index=section_index,
                        section_title=title,
                        message=f"Consecutive narratives share display_role={block.display_role}",
                        block_index=block_index,
                    )
                )
            claims = list(dict.fromkeys(block.claim_ids))
            metrics = list(dict.fromkeys(block.metric_refs))
            if claims and claims == previous_claims:
                size_note = "identical claim set" if len(claims) >= 2 else "shared claim refs"
                warnings.append(
                    EditorialWarning(
                        code=NARRATIVE_CLAIM_OVERLAP,
                        section_index=section_index,
                        section_title=title,
                        message=f"Consecutive narratives have {size_note}",
                        block_index=block_index,
                    )
                )
            if metrics and metrics == previous_metrics:
                warnings.append(
                    EditorialWarning(
                        code=NARRATIVE_METRIC_OVERLAP,
                        section_index=section_index,
                        section_title=title,
                        message="Consecutive narratives bind the same metric_refs",
                        block_index=block_index,
                    )
                )
            if block.display_role == "evidence_interpretation":
                related = (block.related_block_id or "").strip()
                if not related or related not in visual_refs:
                    warnings.append(
                        EditorialWarning(
                            code=INTERPRETATION_WITHOUT_VISUAL,
                            section_index=section_index,
                            section_title=title,
                            message="evidence_interpretation has no related chart or table",
                            block_index=block_index,
                        )
                    )
            previous_role = block.display_role
            previous_claims = claims or None
            previous_metrics = metrics or None
        for block_index, block in enumerate(section.blocks):
            if isinstance(block, ReportEditorCalloutBlock):
                for paragraph in _paragraphs(block.text):
                    paragraphs.append((paragraph, "callout", block_index))
            elif isinstance(block, ReportEditorRecommendationsBlock):
                for item in block.items:
                    for paragraph in _paragraphs(item.text):
                        paragraphs.append((paragraph, "recommendation", block_index))
        seen: dict[str, tuple[str, int | None]] = {}
        for paragraph, origin, block_index in paragraphs:
            key = _normalize(paragraph)
            if not key:
                continue
            if key in seen:
                first_origin, _ = seen[key]
                message = f"Exact paragraph repeats ({first_origin} -> {origin})"
                warnings.append(
                    EditorialWarning(
                        code=EXACT_PARAGRAPH_DUPLICATE,
                        section_index=section_index,
                        section_title=title,
                        message=message,
                        block_index=block_index,
                    )
                )
            else:
                seen[key] = (origin, block_index)
        if _repeated_pattern(narratives):
            warnings.append(
                EditorialWarning(
                    code=REPEATED_BLOCK_PATTERN,
                    section_index=section_index,
                    section_title=title,
                    message="Section repeats the same narrative facts across three or more blocks",
                )
            )
        return warnings


def _visual_refs(section) -> set[str]:
    refs: set[str] = set()
    for block in section.blocks:
        if isinstance(block, (ReportEditorChartBlock, ReportEditorTableBlock)):
            refs.add(block.data_ref)
        elif isinstance(block, ReportEditorVisualGroupBlock):
            for item in block.items:
                refs.add(item.data_ref)
    return refs


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n+", text or "") if part.strip()]


def _normalize(text: str) -> str:
    collapsed = _WHITESPACE.sub("", text or "")
    return _PUNCTUATION.sub("", collapsed)


def _lead_is_dense(text: str, claim_ids: list[str]) -> bool:
    paragraphs = _paragraphs(text)
    sentences = [part for part in _SENTENCE.split(text or "") if part.strip()]
    return len(paragraphs) > 3 or len(sentences) > 4 or len(claim_ids) >= 4


def _repeated_pattern(narratives: list[tuple[int, ReportEditorNarrativeBlock]]) -> bool:
    if len(narratives) < 3:
        return False
    normalized = [_normalize(block.text) for _, block in narratives]
    if len(set(normalized)) == 1 and normalized[0]:
        return True
    claim_sets = [tuple(block.claim_ids) for _, block in narratives]
    nonempty = [item for item in claim_sets if item]
    return len(nonempty) >= 3 and len(set(nonempty)) == 1
