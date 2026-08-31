"""Load the Report Editor system prompt from the project root.

This file is configuration. A missing or empty prompt is a hard error; the
report stage must never fall back to the analysis DA Skill.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.core.errors import AppError

logger = logging.getLogger(__name__)

PROMPT_FILENAME = "REPORT_EDITOR_SYSTEM_PROMPT.md"


class ReportEditorPromptLoader:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or PROJECT_ROOT).resolve()

    @property
    def path(self) -> Path:
        return self.root / PROMPT_FILENAME

    def load(self) -> str:
        path = self.path
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            logger.error("report_editor_prompt_missing path=%s", path)
            raise AppError(
                "report_editor_prompt_missing",
                f"Report Editor system prompt is missing: {path.name}",
                500,
            ) from exc
        except OSError as exc:
            logger.error("report_editor_prompt_unreadable path=%s error=%s", path, exc)
            raise AppError(
                "report_editor_prompt_unreadable",
                f"Report Editor system prompt could not be read: {path.name}",
                500,
            ) from exc
        if not text.strip():
            logger.error("report_editor_prompt_empty path=%s", path)
            raise AppError(
                "report_editor_prompt_empty",
                f"Report Editor system prompt is empty: {path.name}",
                500,
            )
        return text