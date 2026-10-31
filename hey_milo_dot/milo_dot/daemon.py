"""Explicit, paced worker. Logs contain only fixed operational messages."""
import math
import http.client
import signal
import threading

from .worker import tick
from .gwen import export, run


def interval(value):
    value = float(value)
    if not math.isfinite(value) or not 0.05 <= value <= 3600:
        raise ValueError('interval must be 0.05..3600 seconds')
    return value


def review_one(store, endpoint, timeout, stopped):
    for job in store.list():
        if stopped():
            return
        if job['adapter'] != 'bridge' or job['state'] != 'review_pending':
            continue
        bundle = export(job)
        params = (job['id'], bundle['result_hash'])
        # Avoid even a write transaction for previously attempted/imported advice.
        if store.db.execute(
                'SELECT 1 FROM gwen_attempts WHERE job_id=? AND result_hash=? '
                'UNION ALL SELECT 1 FROM gwen_reviews WHERE job_id=? AND result_hash=? LIMIT 1',
                params + params).fetchone():
            continue
        with store.transaction():
            # Reserve before network I/O: crashes and failures need explicit operator retry.
            inserted = store.db.execute(
                'INSERT OR IGNORE INTO gwen_attempts VALUES(?,?)', params).rowcount
        if inserted and not stopped():
            try:
                store.import_gwen(job['id'], run(bundle, endpoint, timeout))
            except (ValueError, KeyError, TypeError, OSError, http.client.HTTPException):
                pass
            return


def serve(store, bridge, poll_interval=5, max_backoff=60, gwen_endpoint=None,
          gwen_timeout=30, stop=None, install_signals=True):
    poll_interval, max_backoff = interval(poll_interval), interval(max_backoff)
    if max_backoff < poll_interval:
        raise ValueError('backoff must be at least poll interval')
    stop = stop or threading.Event()
    previous = {}
    if install_signals:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: stop.set())
    delay = poll_interval
    try:
        while not stop.is_set():
            failed = []
            tick(store, bridge, lease_seconds=300, stopped=stop.is_set,
                 on_error=lambda: failed.append(True))
            if gwen_endpoint and not stop.is_set():
                review_one(store, gwen_endpoint, gwen_timeout, stop.is_set)
            delay = min(max_backoff, delay * 2) if failed else poll_interval
            stop.wait(delay)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
