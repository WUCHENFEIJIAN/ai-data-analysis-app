"""Report generation facade: collect -> edit/fallback -> render -> publish."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.errors import AppError, LLMError, ReportPipelineError, ValidationError
from app.core.logging import diagnostic_extra
from app.llm.base import LLMProvider
from app.services.artifacts import ArtifactService
from app.services.presentation_metadata import PresentationMetadataResolver
from app.services.presentation_preflight import PresentationPreflight
from app.services.report_fallback import FallbackSpecBuilder
from app.services.report_inputs import ReportInputCollector, ReportInputs
from app.services.report_metric_fidelity import eligible_visual_contexts
from app.services.report_pipeline_diagnostics import (
    analytical_visual_count,
    input_counts,
    log_diagnostics,
    report_counts,
)
from app.services.report_planner import ReportEditor
from app.services.report_renderer import ReportRenderer
from app.services.report_spec import ReportSpec
from app.services.report_validator import ReportSpecValidator
from app.services.workspace import PathResolver
from app.skills.loader import SkillLoader

logger = logging.getLogger(__name__)


class ReportService:
    """Stable public facade retained for orchestrator and API callers."""

    def __init__(
        self,
        session: Session,
        resolver: PathResolver,
        skill_loader: SkillLoader,
        provider: LLMProvider,
    ) -> None:
        self.session = session
        self.resolver = resolver
        self.skill_loader = skill_loader
        self.provider = provider

    async def generate(
        self, project_id: str, user_request: str, title: str | None, style: str | None = None
    ) -> str:
        inputs = ReportInputCollector(self.session, self.resolver).collect(
            project_id, user_request, title, style=style
        )
        log_diagnostics(logger, "input", input_counts(inputs), project_id=project_id)
        editor = ReportEditor(self.provider, self.resolver)
        if self.provider is None:
            spec = FallbackSpecBuilder(self.resolver).build(project_id, inputs, style)
            mode = "fallback"
        else:
            try:
                spec = await editor.edit(project_id, inputs)
                spec = editor.validate_and_hydrate(project_id, spec, inputs)
                mode = "llm"
            except ReportPipelineError as exc:
                if exc.code != "report_editor_invalid_output":
                    raise
                logger.warning(
                    "report_editor_fallback code=%s message=%s",
                    exc.code,
                    str(exc)[:200],
                )
                spec = FallbackSpecBuilder(self.resolver).build(project_id, inputs, style)
                spec = editor.validate_and_hydrate(project_id, spec, inputs)
                mode = "fallback"
            except (LLMError, ValidationError, ValueError) as exc:
                logger.warning(
                    "report_editor_fallback code=%s message=%s",
                    getattr(exc, "code", type(exc).__name__),
                    str(exc)[:200],
                )
                spec = FallbackSpecBuilder(self.resolver).build(project_id, inputs, style)
                spec = editor.validate_and_hydrate(project_id, spec, inputs)
                mode = "fallback"
        if spec.provenance.planner_mode != mode:
            spec = spec.model_copy(
                update={"provenance": spec.provenance.model_copy(update={"planner_mode": mode})}
            )
        ReportSpecValidator.validate_assembled(spec, inputs)
        return self._publish(project_id, spec, inputs)

    def _publish(self, project_id: str, spec: ReportSpec, inputs: ReportInputs) -> str:
        spec_path = self.resolver.resolve(project_id, "reports/report_spec.json")
        html_path = self.resolver.resolve(project_id, "reports/report.html")
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        extra = diagnostic_extra(project_id=project_id, stage="REPORT")
        try:
            metric_registry = {item.metric_id: item for item in inputs.metrics}
            spec = PresentationMetadataResolver.apply(spec, metric_registry)
            spec = PresentationPreflight(self.resolver).normalize(project_id, spec)
            log_diagnostics(
                logger,
                "presentation_preflight",
                report_counts(spec),
                project_id=project_id,
            )
            ReportSpecValidator.validate_assembled(spec, inputs)
            eligible = eligible_visual_contexts(inputs)
            final_visuals = analytical_visual_count(spec)
            if spec.provenance.planner_mode == "llm" and eligible and final_visuals == 0:
                raise ReportPipelineError(
                    "analytical_visuals_dropped",
                    "Eligible report-ready analytical evidence produced no final visual",
                    details={
                        "issue": "ANALYTICAL_VISUALS_DROPPED",
                        "eligible_visual_count": len(eligible),
                        "final_analytical_visual_count": 0,
                        "artifact_paths": list(
                            dict.fromkeys(item["data_ref"] for item in eligible)
                        ),
                    },
                )
        except ReportPipelineError:
            raise
        except Exception as exc:
            logger.exception(
                "report_preflight_failed project_id=%s",
                project_id,
                extra={**extra, "error_code": "report_preflight_failed"},
            )
            raise ReportPipelineError(
                "report_preflight_failed",
                details={"exception_type": type(exc).__name__},
            ) from exc
        try:
            document = ReportRenderer(self.resolver).render(project_id, spec)
            ReportRenderer.validate_html(document)
        except ReportPipelineError:
            raise
        except Exception as exc:
            logger.exception(
                "report_render_failed project_id=%s",
                project_id,
                extra={**extra, "error_code": "report_render_failed"},
            )
            raise ReportPipelineError(
                "report_render_failed",
                details={"exception_type": type(exc).__name__},
            ) from exc
        try:
            self._atomic_write_pair(
                (spec_path, spec.model_dump_json(indent=2)), (html_path, document)
            )
            artifacts = ArtifactService(self.session)
            artifacts.register(project_id, "reports/report_spec.json", spec_path.stat().st_size)
            artifacts.register(project_id, "reports/report.html", html_path.stat().st_size)
        except ReportPipelineError:
            raise
        except Exception as exc:
            logger.exception(
                "report_publish_failed project_id=%s",
                project_id,
                extra={**extra, "error_code": "report_publish_failed"},
            )
            raise ReportPipelineError(
                "report_publish_failed",
                details={"exception_type": type(exc).__name__},
            ) from exc
        return "reports/report.html"

    @staticmethod
    def _atomic_write_pair(*items: tuple[Path, str]) -> None:
        temporary_files: list[tuple[Path, Path]] = []
        try:
            for target, content in items:
                fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
                temporary = Path(temporary_name)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary_files.append((temporary, target))
            for temporary, target in temporary_files:
                os.replace(temporary, target)
        finally:
            for temporary, _ in temporary_files:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def extract_html(response: str) -> str:
        content = response.strip()
        lowered = content.lower()
        starts = [
            index for index in (lowered.find("<!doctype html"), lowered.find("<html")) if index >= 0
        ]
        end = lowered.rfind("</html>")
        if not starts or end < min(starts):
            raise AppError(
                "invalid_report_html", "Report did not contain a complete HTML document", 502
            )
        return content[min(starts) : end + len("</html>")]

    @staticmethod
    def validate_html(document: str, evidence: object | None = None) -> None:
        del evidence
        ReportRenderer.validate_html(document)

