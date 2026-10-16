"""Allowlisted JSON logging; free text and arbitrary extras never reach output."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
import sys
import time
import uuid

http_status = ContextVar("http_status", default=None)
request_id = ContextVar("request_id", default=None)
EVENTS = {"startup", "shutdown", "operation_started", "operation_completed", "operation_failed", "invalid_log_level"}
OPERATIONS = {"server", "http", "diagnostics", "logging"}
LOGGER = logging.getLogger("apple_music_mcp")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        event = record.msg if isinstance(record.msg, str) and record.msg in EVENTS and record.name == "apple_music_mcp" else "external_log_redacted"
        data = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname if record.levelname in {"DEBUG", "INFO", "WARNING", "ERROR"} else "ERROR",
            "event": event,
            "operation": record.__dict__.get("operation") if record.__dict__.get("operation") in OPERATIONS else "logging",
        }
        status = http_status.get()
        data["status_code"] = status if type(status) is int and 100 <= status <= 599 else None
        code = record.__dict__.get("error_code", "NONE")
        data["error_code"] = code if code in {"NONE", "OPERATION_FAILED", "HTTP_ERROR", "AUTH_CONFIG_MISSING", "AUTH_CONFIG_INVALID"} else "OPERATION_FAILED"
        rid = request_id.get()
        if isinstance(rid, str) and len(rid) == 32 and all(c in "0123456789abcdef" for c in rid):
            data["request_id"] = rid
        duration = record.__dict__.get("elapsed_ms")
        if isinstance(duration, (int, float)) and 0 <= duration < 1e12:
            data["elapsed_ms"] = round(duration, 3)
        if record.exc_info:
            kind = record.exc_info[0]
            data["exception_type"] = kind.__name__ if kind.__module__ == "builtins" else "ApplicationError"
            tb, lines = record.exc_info[2], []
            while tb is not None:
                lines.append(tb.tb_lineno)
                tb = tb.tb_next
            data["exception_lines"] = lines[-20:]
        return json.dumps(data, allow_nan=False, separators=(",", ":"))


def configure_logging():
    """Own process logging; call only at the executable boundary."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    invalid = level not in {"DEBUG", "INFO", "WARNING", "ERROR"}
    if invalid:
        level = "INFO"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for item in logging.Logger.manager.loggerDict.values():
        if isinstance(item, logging.Logger):
            item.handlers.clear()
            item.propagate = True
            item.setLevel(logging.NOTSET)
    if invalid:
        LOGGER.warning("invalid_log_level", extra={"operation": "logging"})


@contextmanager
def operation(name, error_code="NONE"):
    start = time.perf_counter()
    LOGGER.debug("operation_started", extra={"operation": name})
    try:
        yield
    except KeyboardInterrupt:
        raise
    except BaseException:
        LOGGER.error("operation_failed", extra={"operation": name, "elapsed_ms": (time.perf_counter() - start) * 1000, "error_code": "OPERATION_FAILED"}, exc_info=True)
        raise
    else:
        LOGGER.info("operation_completed", extra={"operation": name, "elapsed_ms": (time.perf_counter() - start) * 1000, "error_code": "HTTP_ERROR" if (http_status.get() or 0) >= 400 else error_code})


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = request_id.set(uuid.uuid4().hex)
        status_token = http_status.set(None)
        async def logged_send(message):
            if message["type"] == "http.response.start":
                http_status.set(message["status"])
            await send(message)
        try:
            with operation("http"):
                await self.app(scope, receive, logged_send)
        finally:
            http_status.reset(status_token)
            request_id.reset(token)
