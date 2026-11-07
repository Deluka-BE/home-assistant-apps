"""Alpha.5: safe, metadata-only agent main-thread diagnostics.

Only imported by the dedicated agent-server --import-modules hook. Never
inspect frames' locals, args, actual code files, paths, environment, or secrets.
"""
from collections import Counter, deque
import json
import re
import sys
import threading
import time

_INTERVAL_SECONDS = 0.5
_WINDOW = 20
_STALL_SAMPLES = 12
_EMIT_EVERY_SECONDS = 10.0
_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,119}\Z")
_FUNCTION = re.compile(r"[A-Za-z_][A-Za-z0-9_.<>]{0,119}\Z")
_APP_PREFIX = "openhands."
_LIBRARY_PREFIXES = ("filelock.", "tenacity.", "libtmux.", "anyio.")
_LIBRARY_EXACT = frozenset({"filelock", "tenacity", "libtmux", "anyio"})
_IDLE_TOP_PREFIXES = ("asyncio.", "selectors", "uvicorn.", "uvloop.", "threading.")
_IDLE_TOP_EXACT = frozenset({"selectors", "select", "threading", "uvloop"})


def _frame_signature(frame):
    """Prefer the OpenHands caller, then a library or __main__ fallback."""
    if frame is None:
        return None
    top = frame.f_globals.get("__name__")
    if not isinstance(top, str):
        return None
    # asyncio's selector wait is healthy; subprocess.communicate() can use
    # exactly the same selector in a *blocking* main-thread call.
    if top in ("selectors", "select") or top.startswith("selectors."):
        parent = frame.f_back
        pname = parent.f_globals.get("__name__") if parent is not None else None
        if not isinstance(pname, str) or pname.startswith(("asyncio.", "uvloop.")):
            return None
    elif top == "threading" or top.startswith("threading."):
        parent = frame.f_back
        pname = parent.f_globals.get("__name__") if parent is not None else None
        if not isinstance(pname, str) or pname.startswith(("asyncio.", "uvloop.")):
            return None
    elif top in _IDLE_TOP_EXACT or top.startswith(_IDLE_TOP_PREFIXES):
        return None
    library = None
    main = None
    for _ in range(32):
        if frame is None:
            break
        module = frame.f_globals.get("__name__")
        func = frame.f_code.co_name
        line = frame.f_lineno
        if (isinstance(module, str) and _MODULE.fullmatch(module)
                and isinstance(func, str) and _FUNCTION.fullmatch(func)
                and type(line) is int and 1 <= line <= 1000000):
            signature = (module, func, line)
            if module.startswith(_APP_PREFIX):
                return signature
            if (module in _LIBRARY_EXACT
                    or module.startswith(_LIBRARY_PREFIXES)) and library is None:
                library = signature
            if module == "__main__" and main is None:
                main = signature
        frame = frame.f_back
    return library or main


def _dominant_signature(window):
    """Return the dominant caller over a rolling 10s window; tolerate line changes."""
    counts = Counter((m, f) for sample in window if sample is not None
                     for m, f, _ in (sample,))
    if not counts:
        return None
    (module, function), amount = counts.most_common(1)[0]
    if amount < _STALL_SAMPLES:
        return None
    linenos = Counter(l for item in window if item is not None
                      for m, f, l in (item,) if (m, f) == (module, function))
    return (module, function, linenos.most_common(1)[0][0]), amount


def _write(record):
    # One write per JSON line, no user- or request-derived content.
    try:
        sys.stderr.write(json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n")
    except (OSError, ValueError):
        pass


def _marker(event):
    _write({"logger": "openhands", "level": "WARNING", "diagnostic_event": event})


def _emit(signature, samples):
    module, function, line = signature
    _write({"logger": "openhands",
            "level": "WARNING",
            "diagnostic_event": "main_thread_stalled",
            "diagnostic_module": module,
            "diagnostic_function": function,
            "diagnostic_lineno": line,
            "diagnostic_samples": min(int(samples), 600)})


def _watch():
    try:
        target = threading.main_thread().ident
        if target is None:
            _marker("sampler_unavailable")
            return
        window = deque(maxlen=_WINDOW)
        last_emitted = 0.0
        while True:
            time.sleep(_INTERVAL_SECONDS)
            signature = _frame_signature(sys._current_frames().get(target))
            if signature is None:
                window.clear()
                continue
            window.append(signature)
            dominant = _dominant_signature(window)
            if dominant is None:
                continue
            now = time.monotonic()
            if now - last_emitted >= _EMIT_EVERY_SECONDS:
                signature, count = dominant
                _emit(signature, count)
                last_emitted = now
    except Exception:
        # Diagnostic failure must never take down the upstream agent-server.
        _marker("sampler_unavailable")


def start():
    try:
        thread = threading.Thread(
            target=_watch, name="ha_canvas_safe_diagnostics", daemon=True)
        thread.start()
    except (OSError, RuntimeError):
        _marker("sampler_unavailable")
        return None
    _marker("sampler_started")
    return thread


# Production hook imports as 'ha_canvas_diagnostic' from /opt/ha-canvas-diag.
# Package imports for offline testing deliberately do not start a thread.
if __name__ == "ha_canvas_diagnostic":
    start()
