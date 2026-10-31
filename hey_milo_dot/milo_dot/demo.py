"""Fake Lex: deterministic local data only; no Bridge, model, shell or network."""


class FakeLex:
    def submit(self, key, payload):
        return {'adapter': 'fake-lex', 'idempotency_key': key, 'echo': payload}


def run_one(store, adapter=None, owner='local-demo', lease_seconds=60):
    job = store.claim(owner, lease_seconds)
    if job is None:
        return None
    store.begin_dispatch(job['id'], owner, job['fence'])
    # Any interruption from here leaves dispatching. Expiry requires reconciliation.
    result = (adapter or FakeLex()).submit(job['idempotency_key'], job['payload'])
    return store.finish(job['id'], owner, job['fence'], result)
