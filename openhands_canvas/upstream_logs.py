"""Body-blind upstream output adapter. Memory bounded independently of line length."""
import asyncio
import json

MAX_LINE = 8192
CHUNK = 4096
COMPONENTS = {'agent_canvas', 'openhands', 'uvicorn', 'uvicorn.error', 'uvicorn.access'}
ERROR_TYPES = {'ValueError', 'RuntimeError', 'OSError', 'PermissionError',
               'FileNotFoundError', 'ConnectionRefusedError', 'TimeoutError', 'SSLError'}
CODES = {'EACCES', 'ENOENT', 'ECONNREFUSED', 'EADDRINUSE', 'ETIMEDOUT'}
BOOT = {'Application startup complete.': 'application_ready',
        'Waiting for application startup.': 'application_starting',
        'Shutting down': 'application_stopping',
        'Application shutdown complete.': 'application_stopped'}


def normalize(line, log, channel):
    level = 'WARNING' if channel == 'stderr' else 'INFO'
    component = 'upstream'
    event = 'unclassified_output'
    fields = []
    try:
        record = json.loads(line)
    except (ValueError, UnicodeError):
        record = None
    if isinstance(record, dict):
        severity = record.get('level', record.get('levelname', record.get('severity')))
        if isinstance(severity, str) and severity.upper() in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
            level = 'ERROR' if severity.upper() == 'CRITICAL' else severity.upper()
        name = record.get('logger', record.get('name', record.get('component')))
        if isinstance(name, str) and name in COMPONENTS:
            component = name
        event = 'structured_output'
        for key, allowed in (('exception_type', ERROR_TYPES), ('code', CODES)):
            value = record.get(key)
            if isinstance(value, str) and value in allowed:
                fields.append(f'{key}={value}')
        status = record.get('status_code')
        if type(status) is int and 100 <= status <= 599:
            fields.append(f'http_status={status}')
    else:
        text = line.decode('utf-8', errors='replace').strip()
        if text.startswith('[agent-canvas] '):
            component = 'agent_canvas'
            text = text[len('[agent-canvas] '):]
        # Only exact boot lines; a severity prefix never licenses arbitrary text.
        for prefix, severity in (('INFO:', 'INFO'), ('WARNING:', 'WARNING'),
                                 ('ERROR:', 'ERROR'), ('DEBUG:', 'DEBUG'), ('CRITICAL:', 'ERROR')):
            if text.startswith(prefix):
                level = severity
                text = text[len(prefix):].strip()
                break
        event = BOOT.get(text, event)
    log.log(level, f'{event} component={component} stream={channel}' +
            ((' ' + ' '.join(fields)) if fields else ''))


async def drain(reader, log, channel):
    pending = bytearray()
    oversized = False
    try:
        while chunk := await reader.read(CHUNK):
            # split is bounded by CHUNK; never use readline on untrusted output.
            parts = chunk.split(b'\n')
            for index, part in enumerate(parts):
                if not oversized:
                    if len(pending) + len(part) <= MAX_LINE:
                        pending.extend(part)
                    else:
                        pending.clear()
                        oversized = True
                        log.error(f'oversized_output component=upstream stream={channel}')
                if index < len(parts) - 1:
                    if not oversized and pending:
                        normalize(bytes(pending), log, channel)
                    pending.clear()
                    oversized = False
            await asyncio.sleep(0)
        if pending:
            normalize(bytes(pending), log, channel)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.error(f'output_drain_failed component=upstream stream={channel}')
        raise
