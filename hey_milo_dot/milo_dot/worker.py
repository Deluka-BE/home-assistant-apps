"""One persistent Bridge step; network operations never hold a write transaction."""
from .bridge import BridgeError


def tick(store, bridge, owner='bridge-tick', lease_seconds=60, stopped=lambda: False, on_error=lambda: None):
    job = store.claim(owner, lease_seconds, adapter='bridge')
    if job is None:
        return None
    args = (job['id'], owner, job['fence'])
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
