from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_request_id_pattern = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_sensitive_fragments = (
    "authorization",
    "body",
    "content",
    "cookie",
    "credential",
    "key",
    "password",
    "secret",
    "token",
)


def redact(value: Any, key: str = "") -> Any:
    if any(fragment in key.lower() for fragment in _sensitive_fragments):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    _structured_fields = (
        "service",
        "method",
        "path",
        "status_code",
        "duration_ms",
        "dependencies",
        "details",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or _request_id.get()
        if request_id:
            payload["request_id"] = request_id
        for field in self._structured_fields:
            field_value = getattr(record, field, None)
            if field_value is not None:
                payload[field] = redact(field_value, field)
        if record.exc_info:
            payload["exception"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_structured_logging() -> None:
    root_logger = logging.getLogger()
    if not any(getattr(handler, "knowledge_json", False) for handler in root_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler.knowledge_json = True  # type: ignore[attr-defined]
        root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)


def _request_id_from_header(value: str | None) -> str:
    if value and _request_id_pattern.fullmatch(value):
        return value
    return str(uuid4())


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = _request_id_from_header(request.headers.get("X-Request-ID"))
        token = _request_id.set(request_id)
        started_at = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            logging.getLogger("knowledge.request").info(
                "request_completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                },
            )
            _request_id.reset(token)