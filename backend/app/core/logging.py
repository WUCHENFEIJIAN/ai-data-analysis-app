import json
import logging
from datetime import UTC, datetime

_SAFE_EXTRA_FIELDS = ('run_id', 'project_id', 'stage', 'error_code')


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            'timestamp': datetime.now(UTC).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        for field in _SAFE_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value not in (None, ''):
                payload[field] = value
        if record.exc_info:
            exc_type = record.exc_info[0]
            payload['exception_type'] = exc_type.__name__ if exc_type else None
            payload['traceback'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def diagnostic_extra(
    *,
    run_id: str | None = None,
    project_id: str | None = None,
    stage: str | None = None,
    error_code: str | None = None,
) -> dict[str, str]:
    extra = {
        'run_id': run_id,
        'project_id': project_id,
        'stage': stage,
        'error_code': error_code,
    }
    return {key: value for key, value in extra.items() if value}


def configure_logging(environment: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if environment == 'development' else logging.INFO)
