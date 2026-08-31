"""Report Editor: choose what to show from existing analysis results."""

from __future__ import annotations

import json
import logging

from app.core.errors import LLMError, ReportPipelineError, ValidationError
from app.llm.base import LLMProvider
from app.services.report_editor_assembler import ReportEditorAssembler
from app.services.report_editor_prompt import ReportEditorPromptLoader
from app.services.report_editor_spec import ReportEditorRevision, ReportEditorSpec
from app.services.report_editorial_context import EditorialContextBuilder
from app.services.report_editorial_lint import EditorialLint
from app.services.report_editorial_revision import affected_sections, merge_revision
from app.services.report_inputs import ReportInputs
from app.services.report_pipeline_diagnostics import (
    editor_counts,
    log_diagnostics,
    report_counts,
)
from app.services.report_spec import ReportSpec
from app.services.report_validator import ReportSpecValidator
from app.services.workspace import PathResolver

logger = logging.getLogger(__name__)


class ReportEditor:
    def __init__(
        self,
        provider: LLMProvider,
        resolver: PathResolver,
        prompt_loader: ReportEditorPromptLoader | None = None,
    ) -> None:
        self.provider = provider
        self.resolver = resolver
        self.prompt_loader = prompt_loader or ReportEditorPromptLoader()

    async def plan(self, project_id: str, inputs: ReportInputs) -> ReportSpec:
        return await self.edit(project_id, inputs)

    async def edit(self, project_id: str, inputs: ReportInputs) -> ReportSpec:
        del project_id
        system_prompt = self.prompt_loader.load()
        context = EditorialContextBuilder.build(inputs)
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            },
        ]
        draft = await self._complete_with_schema_repair(messages)
        log_diagnostics(logger, "draft_1", editor_counts(draft))
        result = ReportSpecValidator.validate(draft, inputs)
        log_diagnostics(
            logger,
            "validation_1",
            editor_counts(result.spec),
            issue_codes=[issue.code for issue in result.issues],
            rejected_visuals=sum(
                item.startswith(("chart:", "table:")) for item in result.dropped
            ),
        )
        if result.issues:
            logger.info(
                "report_editor_retry issues=%s",
                [issue.code for issue in result.issues],
            )
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "repair": (
                                "Make the minimum necessary repair to previous_draft. Preserve "
                                "every legal section, visual, narrative, and recommendation. "
                                "For an illegal visual, first correct its field/metric selection "
                                "from the supplied eligible report-ready visual contexts; next "
                                "replace only that visual with another legal supplied visual. "
                                "Delete one visual only when it cannot be legally repaired or "
                                "replaced. Never delete all analytical visuals or convert "
                                "chart_led/table_led sections to narrative-only to evade metric "
                                "or interpretation contracts. Do not invent data, fields, metric "
                                "ids, or claim ids."
                            ),
                            "previous_draft": draft.model_dump(mode="json"),
                            "issues": [issue.as_dict() for issue in result.issues],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
            draft = await self._complete_with_schema_repair(messages)
            log_diagnostics(logger, "draft_2", editor_counts(draft))
            result = ReportSpecValidator.validate(draft, inputs)
            log_diagnostics(
                logger,
                "validation_2",
                editor_counts(result.spec),
                issue_codes=[issue.code for issue in result.issues],
                rejected_visuals=sum(
                    item.startswith(("chart:", "table:")) for item in result.dropped
                ),
            )
        if not result.spec.sections:
            raise ReportPipelineError(
                "report_editor_invalid_output",
                "Report Editor produced no usable sections",
            )
        interpretation_issues = [
            issue for issue in result.issues if issue.code == "interpretation.missing"
        ]
        if interpretation_issues:
            raise ReportPipelineError(
                "report_editor_invalid_output",
                "Report Editor omitted required evidence interpretation after one retry",
                details={"issues": [issue.as_dict() for issue in interpretation_issues]},
            )
        if result.issues:
            logger.warning(
                "report_editor_dropped items=%s issues=%s",
                result.dropped,
                [issue.code for issue in result.issues],
            )
        lint = EditorialLint.check(result.spec)
        if lint.should_revise():
            revised = await self._revise_once(result.spec, lint, inputs)
            if revised is not None:
                revised_result = ReportSpecValidator.validate(revised, inputs)
                if revised_result.spec.sections:
                    result = revised_result
                    lint = EditorialLint.check(result.spec)
        if lint.warnings:
            logger.info(
                "report_editorial_lint warnings=%s",
                [item.code for item in lint.warnings],
            )
        spec = ReportEditorAssembler().assemble(result.spec, inputs, planner_mode="llm")
        log_diagnostics(logger, "assembled", report_counts(spec))
        ReportSpecValidator.validate_assembled(spec, inputs)
        return spec


    async def _revise_once(self, spec: ReportEditorSpec, lint, inputs: ReportInputs):
        sections = affected_sections(spec, lint)
        if not sections:
            return None
        finding_ids = {
            finding_id
            for section in sections
            for finding_id in section.finding_refs
        }
        payload = {
            "editorial_revision": True,
            "instruction": (
                "Preserve facts. Preserve metric refs. Preserve data refs. "
                "Preserve chart/table choices unless necessary. "
                "Only rewrite the affected narrative blocks to remove repetition. "
                "You may omit supporting_narrative or callout if they add no new "
                "information. Do not regenerate the whole report. Return only the "
                "listed sections."
            ),
            "warnings": [
                {
                    "code": item.code,
                    "section_title": item.section_title,
                    "message": item.message,
                }
                for item in lint.warnings
            ],
            "sections": [section.model_dump() for section in sections],
            "findings": [
                {
                    "id": finding.id,
                    "title": finding.title,
                    "claims": [
                        {"claim_id": claim.claim_id, "statement": claim.statement}
                        for claim in finding.claims
                    ],
                }
                for finding in inputs.findings.findings
                if finding.id in finding_ids
            ],
        }
        messages = [
            {"role": "system", "content": self.prompt_loader.load()},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        ]
        try:
            revision = await self.provider.structured_chat(messages, ReportEditorRevision)
        except (LLMError, ValidationError) as exc:
            logger.warning(
                "report_editorial_revision_skipped code=%s",
                getattr(exc, "code", type(exc).__name__),
            )
            return None
        return merge_revision(spec, revision)

    async def _complete_with_schema_repair(
        self, messages: list[dict[str, str]]
    ) -> ReportEditorSpec:
        working = list(messages)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return await self._complete(working)
            except (LLMError, ValidationError, ReportPipelineError) as exc:
                last_error = exc
                if isinstance(exc, LLMError) and exc.code not in {
                    "llm_invalid_output",
                    "validation_error",
                }:
                    raise
                logger.info(
                    "report_editor_schema_retry attempt=%s code=%s",
                    attempt + 1,
                    getattr(exc, "code", type(exc).__name__),
                )
                if attempt >= 1:
                    break
                working = [
                    *working,
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "repair": (
                                    "Delete illegal fields. Do not move them onto other "
                                    "blocks. Do not invent claim ids that are not in the "
                                    "input. callout may only contain type, tone, title, "
                                    "and text. claim_ids may only appear on sections, "
                                    "narrative blocks, and recommendation source_claim_ids."
                                ),
                                "error": str(exc)[:500],
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                ]
        raise ReportPipelineError(
            "report_editor_invalid_output",
            details={
                "exception_type": type(last_error).__name__ if last_error else None,
                "error": str(last_error)[:200] if last_error else "invalid editor output",
            },
        ) from last_error

    async def _complete(self, messages: list[dict[str, str]]) -> ReportEditorSpec:
        try:
            return await self.provider.structured_chat(messages, ReportEditorSpec)
        except LLMError:
            raise
        except Exception as exc:
            raise ValidationError(f"Report Editor structured output is invalid: {exc}") from exc

    def validate_and_hydrate(
        self, project_id: str, spec: ReportSpec, inputs: ReportInputs
    ) -> ReportSpec:
        del project_id
        by_path = {entry.path: entry for entry in inputs.catalog}
        sources = []
        for source in spec.sources:
            entry = by_path.get(source.artifact_path)
            if entry is None:
                raise ValidationError(
                    f"Report spec references an unknown artifact: {source.artifact_path}"
                )
            sources.append(
                source.model_copy(update={"sha256": entry.sha256, "media_type": entry.media_type})
            )
        metrics = {item.metric_id: item for item in inputs.metrics}
        kpis = []
        for kpi in spec.kpis:
            definition = kpi.metric_definition or metrics.get(kpi.metric)
            if definition is None:
                kpis.append(kpi)
                continue
            kpis.append(
                kpi.model_copy(
                    update={
                        "metric_definition": definition,
                        "definition_note": kpi.definition_note or definition.definition,
                        "display_label": kpi.display_label or definition.label,
                    }
                )
            )
        return spec.model_copy(update={"sources": sources, "kpis": kpis})


EditorialPlanner = ReportEditor
ReportPlanner = ReportEditor
