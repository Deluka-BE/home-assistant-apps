"""SQLite transactions are the authority; no job text is executable."""
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


TRANSITIONS = {
    'queued': {'claimed'},
    'claimed': {'queued', 'dispatching'},
    'dispatching': {'review_pending', 'reconciliation_required', 'remote_pending'},
    'remote_pending': {'review_pending', 'remote_failed'},
    'remote_failed': set(),
    'reconciliation_required': {'review_pending'},
    'review_pending': {'succeeded', 'rejected'},
    'succeeded': set(),
    'rejected': set(),
}


class Conflict(ValueError):
    pass


class StaleClaim(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Store:
    def __init__(self, path, clock=time.time):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL, state TEXT NOT NULL,
                fence INTEGER NOT NULL DEFAULT 0, owner TEXT, lease_until REAL,
                result TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(id), at REAL NOT NULL,
                kind TEXT NOT NULL, data TEXT NOT NULL
            );
        ''')

        # Version 0 is the phase-1 schema. Migration is atomic and retains all rows.
        with self.transaction():
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 2, 3, 4):
                raise ValueError('unsupported database version')
            if version == 0:
                self.db.execute("ALTER TABLE jobs ADD COLUMN adapter TEXT NOT NULL DEFAULT 'fake'")
                self.db.execute('ALTER TABLE jobs ADD COLUMN remote_id TEXT')
                self.db.execute('ALTER TABLE jobs ADD COLUMN remote_status TEXT')
                self.db.execute('PRAGMA user_version=2')
            if version in (0, 2):
                self.db.execute('ALTER TABLE jobs ADD COLUMN claim_order INTEGER NOT NULL DEFAULT 0')
                self.db.execute('CREATE TABLE claim_counter (value INTEGER NOT NULL)')
                self.db.execute('INSERT INTO claim_counter VALUES (0)')
                self.db.execute('PRAGMA user_version=3')
            self.db.execute("""CREATE TABLE IF NOT EXISTS gwen_reviews (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
                result_hash TEXT NOT NULL, model TEXT NOT NULL, at REAL NOT NULL,
                advice TEXT NOT NULL, evidence TEXT NOT NULL
            )""")
            self.db.execute('CREATE TABLE IF NOT EXISTS gwen_attempts (job_id TEXT NOT NULL REFERENCES jobs(id), result_hash TEXT NOT NULL, PRIMARY KEY(job_id,result_hash))')
            self.db.execute('PRAGMA user_version=4')

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def _event(self, job_id, kind, data):
        self.db.execute('INSERT INTO events(job_id,at,kind,data) VALUES(?,?,?,?)',
                        (job_id, self.clock(), kind, canonical(data)))

    def get(self, job_id):
        row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        out = dict(row)
        out['payload'] = json.loads(out['payload'])
        out['result'] = json.loads(out['result']) if out['result'] is not None else None
        return out

    def list(self):
        return [self.get(row[0]) for row in self.db.execute(
            'SELECT id FROM jobs ORDER BY created_at,id').fetchall()]

    def events(self, job_id):
        self.get(job_id)
        return [dict(row) | {'data': json.loads(row['data'])} for row in
                self.db.execute('SELECT * FROM events WHERE job_id=? ORDER BY seq', (job_id,))]

    def create(self, key, payload, adapter="fake"):
        if not isinstance(key, str) or not key.strip():
            raise ValueError('idempotency key must be nonempty')
        if adapter not in ('fake', 'bridge'):
            raise ValueError('invalid adapter')
        if adapter == 'bridge':
            from .bridge import validate
            if not isinstance(payload, dict) or set(payload) != {'kind', 'task', 'idempotency_key'}:
                raise ValueError('invalid submit payload')
            validate(key, payload['kind'], payload['task'])
            if payload['idempotency_key'] != key:
                raise ValueError('key mismatch')
        encoded = canonical(payload)
        with self.transaction():
            old = self.db.execute('SELECT id,payload,adapter FROM jobs WHERE idempotency_key=?',
                                  (key,)).fetchone()
            if old:
                if old['payload'] != encoded or old['adapter'] != adapter:
                    raise Conflict('idempotency key already has a different payload')
                return self.get(old['id'])
            job_id = str(uuid.uuid4())
            now = self.clock()
            self.db.execute('''INSERT INTO jobs
                (id,idempotency_key,payload,state,created_at,updated_at) VALUES(?,?,?,'queued',?,?)''',
                            (job_id, key, encoded, now, now))
            self.db.execute('UPDATE jobs SET adapter=? WHERE id=?', (adapter, job_id))
            self._event(job_id, 'created', {'state': 'queued'})
            return self.get(job_id)

    def _change(self, job, state, detail=None):
        if state not in TRANSITIONS[job['state']]:
            raise ValueError(f"invalid transition: {job['state']} -> {state}")
        self.db.execute('UPDATE jobs SET state=?,updated_at=? WHERE id=?',
                        (state, self.clock(), job['id']))
        self._event(job['id'], 'state_changed',
                    {'from': job['state'], 'to': state, **(detail or {})})

    def _recover(self):
        rows = self.db.execute("""SELECT id FROM jobs WHERE state IN ('claimed','dispatching')
                               AND lease_until<=?""", (self.clock(),)).fetchall()
        for row in rows:
            job = self.get(row['id'])
            target = 'queued' if job['state'] == 'claimed' else 'reconciliation_required'
            self._change(job, target, {'reason': 'lease_expired', 'fence': job['fence']})
            self.db.execute('UPDATE jobs SET owner=NULL,lease_until=NULL WHERE id=?', (job['id'],))
        return len(rows)

    def recover(self):
        with self.transaction():
            return self._recover()

    def claim(self, owner, lease_seconds=60, adapter="fake"):
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError('owner must be nonempty')
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError('lease must be positive and finite')
        with self.transaction():
            self._recover()
            row = self.db.execute("""SELECT id FROM jobs WHERE adapter=? AND
                (state='queued' OR (state='remote_pending' AND (owner IS NULL OR lease_until<=?)))
                ORDER BY claim_order,created_at,id LIMIT 1""", (adapter, self.clock())).fetchone()
            if row is None:
                return None
            job = self.get(row['id'])
            fence = job['fence'] + 1
            if job['state'] == 'queued':
                self._change(job, 'claimed', {'owner': owner, 'fence': fence})
            # Rotate atomically with the lease; timestamps need not advance.
            self.db.execute('UPDATE claim_counter SET value=value+1')
            self.db.execute('''UPDATE jobs SET owner=?,fence=?,lease_until=?,
                            claim_order=(SELECT value FROM claim_counter) WHERE id=?''',
                            (owner, fence, self.clock() + lease_seconds, job['id']))
            return self.get(job['id'])

    def _guard(self, job_id, owner, fence):
        job = self.get(job_id)
        if (job['owner'] != owner or job['fence'] != fence or
                job['lease_until'] is None or job['lease_until'] <= self.clock()):
            raise StaleClaim('claim expired or superseded')
        return job

    def begin_dispatch(self, job_id, owner, fence):
        with self.transaction():
            job = self._guard(job_id, owner, fence)
            self._change(job, 'dispatching', {'fence': fence})
            return self.get(job_id)

    def finish(self, job_id, owner, fence, result):
        encoded = canonical(result)
        with self.transaction():
            job = self._guard(job_id, owner, fence)
            self._change(job, 'review_pending', {'fence': fence})
            self.db.execute('UPDATE jobs SET result=?,owner=NULL,lease_until=NULL WHERE id=?',
                            (encoded, job_id))
            return self.get(job_id)

    def reconcile(self, job_id, result, evidence):
        """Explicit operator observation of an ambiguous dispatch; never resend."""
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError('reconciliation evidence required')
        encoded = canonical(result)
        with self.transaction():
            job = self.get(job_id)
            if job['state'] != 'reconciliation_required':
                raise ValueError('job does not require reconciliation')
            self._change(job, 'review_pending', {'evidence': evidence})
            self.db.execute('UPDATE jobs SET result=? WHERE id=?', (encoded, job_id))
            return self.get(job_id)

    def review(self, job_id, decision, reviewer, reason):
        if decision not in ('approve', 'reject') or not reviewer.strip() or not reason.strip():
            raise ValueError('explicit approve/reject, reviewer and reason required')
        with self.transaction():
            job = self.get(job_id)
            self._change(job, 'succeeded' if decision == 'approve' else 'rejected',
                         {'reviewer': reviewer, 'reason': reason, 'decision': decision})
            return self.get(job_id)

    def remote_update(self, job_id, owner, fence, remote_id=None, status=None, result=None, ambiguous=False):
        from .bridge import FAILURES, STATUSES, job_id as validate_id
        with self.transaction():
            job = self._guard(job_id, owner, fence)
            if job['adapter'] != 'bridge':
                raise ValueError('Bridge job required')
            if ambiguous:
                self._change(job, 'reconciliation_required')
            elif remote_id is not None:
                validate_id(remote_id)
                self._change(job, 'remote_pending')
                self.db.execute('UPDATE jobs SET remote_id=? WHERE id=?', (remote_id, job_id))
            elif job['state'] == 'remote_pending':
                if status is not None and (not isinstance(status, str) or status not in STATUSES):
                    raise ValueError('invalid remote status')
                if status in FAILURES:
                    self._change(job, 'remote_failed')
                elif result is not None:
                    if status != 'completed':
                        raise ValueError('completed result required')
                    self._change(job, 'review_pending')
                self.db.execute('UPDATE jobs SET remote_status=?,result=? WHERE id=?',
                                (status, canonical(result) if result is not None else None, job_id))
            else:
                raise ValueError('invalid remote update')
            self.db.execute('UPDATE jobs SET owner=NULL,lease_until=NULL WHERE id=?', (job_id,))
            self._event(job_id, 'bridge_observation', {'status': status, 'ambiguous': ambiguous})
            return self.get(job_id)

    def import_gwen(self, job_id, envelope):
        from .gwen import MODEL, advice, bounded, digest, export
        bounded(envelope)
        if not isinstance(envelope, dict) or set(envelope) != {'version', 'job_id', 'result_hash', 'response'}:
            raise ValueError('invalid review envelope')
        value = advice(envelope['response'])
        with self.transaction():
            expected = export(self.get(job_id))
            if (envelope['version'] != 1 or envelope['job_id'] != job_id or
                    envelope['result_hash'] != expected['result_hash']):
                raise ValueError('stale or mismatched review')
            review_id = digest({'job_id': job_id, 'result_hash': expected['result_hash'],
                                'model': MODEL, 'advice': value})
            self.db.execute("""INSERT OR IGNORE INTO gwen_reviews
                VALUES(?,?,?,?,?,?,?)""", (review_id, job_id, expected['result_hash'], MODEL,
                self.clock(), canonical(value), canonical(envelope['response'])))
            row = dict(self.db.execute('SELECT * FROM gwen_reviews WHERE id=?', (review_id,)).fetchone())
            for field in ('advice', 'evidence'):
                row[field] = json.loads(row[field])
            return row
