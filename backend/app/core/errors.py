from typing import Any


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class NotFoundError(AppError):
    def __init__(self, resource: str) -> None:
        super().__init__("not_found", f"{resource} not found", 404)


class ValidationError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("validation_error", message, 422)


class LLMError(AppError):
    def __init__(
        self,
        message: str,
        code: str = "llm_error",
        status_code: int = 502,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, status_code, details)


REPORT_ERROR_MESSAGES = {
    "report_editor_invalid_output": "Report editor returned invalid output",
    "report_reference_invalid": "Report references a missing claim or insight",
    "report_preflight_failed": "Report presentation preflight failed",
    "report_render_failed": "Report rendering failed",
    "report_publish_failed": "Report publishing failed",
    "analytical_visuals_dropped": "Eligible analytical visuals were dropped",
}

_BLOCKED_DIAGNOSTIC_KEYS = {
    "api_key",
    "authorization",
    "token",
    "secret",
    "password",
    "prompt",
    "messages",
    "content",
    "request",
    "headers",
}


class ReportPipelineError(AppError):
    """User-safe report-stage failure with a stable error code."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code,
            message or REPORT_ERROR_MESSAGES.get(code, "Report generation failed"),
            status_code,
            details,
        )
        self.stage = "REPORT"


def sanitize_diagnostics(details: dict[str, Any] | None, *, limit: int = 200) -> dict[str, Any]:
    """Keep structured failure context without prompts, keys, or huge blobs."""

    if not details:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        lowered = str(key).lower()
        if lowered in _BLOCKED_DIAGNOSTIC_KEYS or any(
            token in lowered for token in ("api_key", "authorization", "prompt", "secret", "token")
        ):
            continue
        cleaned[key] = _sanitize_diagnostic_value(value, limit=limit)
    return cleaned


def _sanitize_diagnostic_value(value: Any, *, limit: int) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (list, tuple)):
        return [_sanitize_diagnostic_value(item, limit=limit) for item in list(value)[:20]]
    if isinstance(value, dict):
        return sanitize_diagnostics(value, limit=limit)
    return type(value).__name__
