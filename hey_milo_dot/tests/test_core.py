import concurrent.futures
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from milo_dot.demo import run_one
from milo_dot.store import Conflict, StaleClaim, Store


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'jobs.sqlite3'
        self.now = 1000
        self.store = self.open()

    def open(self):
        return Store(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def create(self):
        return self.store.create('stable-key', {'task': 'local demo'})

    def test_reopen_duplicate_and_conflict(self):
        job = self.create()
        self.store.close()
        self.store = self.open()
        self.assertEqual(self.create(), job)
        self.assertEqual(len(self.store.events(job['id'])), 1)
        with self.assertRaises(Conflict):
            self.store.create('stable-key', {'task': 'different'})
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.get(job['id'])['payload'], {'task': 'local demo'})

    def test_invalid_transitions(self):
        job = self.create()
        with self.assertRaises(ValueError):
            self.store.review(job['id'], 'approve', 'human', 'checked')
        claim = self.store.claim('worker')
        with self.assertRaises(ValueError):
            self.store.finish(job['id'], 'worker', claim['fence'], {})
        self.store.begin_dispatch(job['id'], 'worker', claim['fence'])
        with self.assertRaises(ValueError):
            self.store.begin_dispatch(job['id'], 'worker', claim['fence'])
        self.assertEqual(self.store.get(job['id'])['state'], 'dispatching')

    def test_two_connections_race(self):
        self.create()
        barrier = threading.Barrier(2)

        def claim(owner):
            store = self.open()
            try:
                barrier.wait(timeout=5)
                return store.claim(owner)
            finally:
                store.close()

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(claim, owner) for owner in ('a', 'b')]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(len(self.store.events(results[0]['id'] if results[0] else results[1]['id'])), 2)

    def test_local_claim_recovery_and_fencing(self):
        job = self.create()
        first = self.store.claim('same-owner', 5)
        self.now += 5
        other = self.open()
        try:
            second = other.claim('same-owner', 5)
            self.assertEqual(second['fence'], first['fence'] + 1)
            with self.assertRaises(StaleClaim):
                self.store.begin_dispatch(job['id'], 'same-owner', first['fence'])
            other.begin_dispatch(job['id'], 'same-owner', second['fence'])
        finally:
            other.close()

    def test_interrupted_dispatch_never_repeated(self):
        self.create()
        calls = []

        class Interrupted:
            def submit(self, key, payload):
                calls.append(key)
                raise RuntimeError('crash after possible external acceptance')

        with self.assertRaises(RuntimeError):
            run_one(self.store, Interrupted(), lease_seconds=5)
        self.store.close()
        self.store = self.open()
        self.assertIsNone(run_one(self.store, Interrupted()))
        self.now += 5
        self.assertEqual(self.store.recover(), 1)
        self.assertEqual(self.store.recover(), 0)
        self.assertIsNone(run_one(self.store, Interrupted()))
        job = self.store.list()[0]
        self.assertEqual(job['state'], 'reconciliation_required')
        self.assertEqual(len(calls), 1)
        with self.assertRaises(StaleClaim):
            self.store.finish(job['id'], 'local-demo', job['fence'], {})
        result = self.store.reconcile(job['id'], {'observed': 'done'}, 'operator checked remote record')
        self.assertEqual(result['state'], 'review_pending')

    def test_expired_dispatch_cannot_finish_even_before_recovery(self):
        self.create()
        job = self.store.claim('worker', 1)
        self.store.begin_dispatch(job['id'], 'worker', job['fence'])
        self.now += 1
        with self.assertRaises(StaleClaim):
            self.store.finish(job['id'], 'worker', job['fence'], {})
        self.store.recover()
        self.assertEqual(self.store.get(job['id'])['state'], 'reconciliation_required')

    def test_result_and_explicit_review_durable(self):
        self.create()
        job = run_one(self.store)
        self.assertEqual(job['state'], 'review_pending')
        self.store.close()
        self.store = self.open()
        self.assertEqual(self.store.get(job['id'])['result']['adapter'], 'fake-lex')
        self.assertIsNone(run_one(self.store))
        approved = self.store.review(job['id'], 'approve', 'operator', 'result checked')
        self.assertEqual(approved['state'], 'succeeded')
        with self.assertRaises(ValueError):
            self.store.review(job['id'], 'reject', 'operator', 'second decision')
        rejected = self.store.create('second', {})
        run_one(self.store)
        self.assertEqual(self.store.review(rejected['id'], 'reject', 'operator', 'incorrect')['state'], 'rejected')

    def test_state_event_rollback_together(self):
        job = self.create()
        self.store.db.execute("""CREATE TRIGGER fail_event BEFORE INSERT ON events
                              BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.claim('worker')
        self.assertEqual(self.store.get(job['id'])['state'], 'queued')
        self.assertEqual(self.store.get(job['id'])['fence'], 0)
        self.assertEqual(len(self.store.events(job['id'])), 1)


if __name__ == '__main__':
    unittest.main()
