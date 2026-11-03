"""One persistent Bridge step; network operations never hold a write transaction."""
from .bridge import BridgeError


def tick(store, bridge, owner='bridge-tick', lease_seconds=60, stopped=lambda: False, on_error=lambda: None, pc=None, wake_wait=None):
    job = store.claim(owner, lease_seconds, adapter='bridge')
    if job is None:
        return None
    args = (job['id'], owner, job['fence'])
    if pc is not None:
        from .pc import ensure_pc_awake
        options = {'wait': wake_wait} if wake_wait is not None else {}
        readiness = ensure_pc_awake(store, pc, timeout=min(90, lease_seconds / 2),
                                    stopped=stopped, **options)
        if not readiness['ok'] or stopped():
            on_error()
            return store.pc_deferred(*args, readiness=readiness)
    if job['state'] == 'claimed':
        store.begin_dispatch(*args)
        try:
            remote_id = bridge.submit(job['payload'])
        except BridgeError:
            on_error()
            return store.remote_update(*args, ambiguous=True)
        # A crash or lost lease before this commit requires reconciliation.
        return store.remote_update(*args, remote_id=remote_id)
    try:
        status = bridge.get(job['remote_id'])
        result = bridge.result(job['remote_id']) if status == 'completed' and not stopped() else None
    except BridgeError:
        on_error()
        # Status/result reads are safe to repeat; retain the original remote ID.
        return store.remote_update(*args, status=job['remote_status'])
    return store.remote_update(*args, status=status, result=result)
