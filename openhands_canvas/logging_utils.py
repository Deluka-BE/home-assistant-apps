"""Logging voor korte, bekende events; geen chat-, request- of exceptionbodies."""

import logging
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

LOG_LEVELS = {name: getattr(logging, name) for name in ('DEBUG', 'INFO', 'WARNING', 'ERROR')}
DEFAULT_LOG_LEVEL = 'INFO'


def _level(value):
    if not isinstance(value, str) or value.upper() not in LOG_LEVELS:
        raise ValueError('log_level must be DEBUG, INFO, WARNING or ERROR')
    return LOG_LEVELS[value.upper()]


_BEARER = re.compile(r'\bBearer\s+[^\s,;\'"}]+', re.IGNORECASE)
_JWT = re.compile(r'\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b')
_ASSIGNMENT = re.compile(
    r'''(\b(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|token|password)\b["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}]+)''',
    re.IGNORECASE,
)


def redact(message):
    """Best effort voor bekende notaties, geen algemene detector van geheimen."""
    message = _BEARER.sub('Bearer [REDACTED]', message)
    message = _JWT.sub('[REDACTED]', message)
    return _ASSIGNMENT.sub(lambda match: match[1] + '[REDACTED]', message)


class EventFormatter(logging.Formatter):
    def __init__(self, timezone):
        super().__init__('%(asctime)s %(levelname)s [%(name)s] %(message)s')
        self.timezone = ZoneInfo(timezone)

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, self.timezone).isoformat(timespec='seconds')

    def format(self, record):
        # Never append traceback/stack bodies, even for direct logger calls.
        from copy import copy
        safe = copy(record)
        safe.msg = redact(record.getMessage()).replace('\r', r'\r').replace('\n', r'\n')
        safe.args = ()
        safe.exc_info = safe.exc_text = safe.stack_info = None
        return super().format(safe)


class Logger:
    def __init__(self, component, level=DEFAULT_LOG_LEVEL, timezone=None):
        threshold = _level(level)
        formatter = EventFormatter(timezone or os.environ.get('TZ') or 'Europe/Brussels')
        # A private logger keeps initialization independent of root/other components.
        self.logger = logging.Logger(component, threshold)
        self.logger.propagate = False
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def log(self, level, message):
        self.logger.log(_level(level), message)

    def debug(self, message):
        self.log('DEBUG', message)

    def info(self, message):
        self.log('INFO', message)

    def warning(self, message):
        self.log('WARNING', message)

    def error(self, message):
        self.log('ERROR', message)

    def failure(self, stage, exc):
        # Only built-in exception names; custom class names can contain user data.
        kind = next((base.__name__ for base in type(exc).__mro__
                     if base.__module__ in ('builtins', 'ssl', 'zoneinfo')), 'Exception')
        self.error(f'startup_failed stage={stage} exception_type={kind}')
