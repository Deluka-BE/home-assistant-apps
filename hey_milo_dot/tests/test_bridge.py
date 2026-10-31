import json
import io
import sqlite3
import ssl
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from milo_dot.bridge import Bridge, BridgeError, validate
from milo_dot.demo import run_one
from milo_dot.store import Store, StaleClaim
from milo_dot.worker import tick
from milo_dot.__main__ import external_path

RID = str(uuid.uuid4())


class Stub:
    def __init__(self):
        self.calls = []
        self.responses = []
        self.store = None

    def __call__(self, path, body, headers):
        if self.store:
            assert not self.store.db.in_transaction
        self.calls.append((path, json.loads(body), headers))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def response(value, status=200):
    return status, json.dumps(value).encode()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.db'
        self.now = 100
        self.store = Store(self.path, clock=lambda: self.now)
        self.stub = Stub()
        self.stub.store = self.store
        self.bridge = Bridge('https://bridge.invalid', lambda: 'dummy-test-token', transport=self.stub)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def create(self):
        return self.store.create('bridge-1', validate('bridge-1', 'research', '[PROJECT:hey-milo-dot] test'), 'bridge')

    def submit(self):
        self.stub.responses.append(response({'job_id': RID}))
        return tick(self.store, self.bridge)

    def test_submit_reopen_poll_result_and_isolation(self):
        fake = self.store.create('fake', {'task': 'fake'})
        job = self.create()
        submitted = self.submit()
        self.assertEqual(submitted['remote_id'], RID)
        self.assertEqual(self.stub.calls[0][1], job['payload'])
        self.assertEqual(set(self.stub.calls[0][1]), {'kind', 'task', 'idempotency_key'})
        self.assertEqual(run_one(self.store)['id'], fake['id'])
        self.assertIsNone(run_one(self.store))
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.stub.store = self.store
        self.stub.responses.extend([response({'job_id': RID, 'status': 'completed'}),
                                    response({'summary': 'done', 'findings': ['checked']})])
        done = tick(self.store, self.bridge)
        self.assertEqual(done['state'], 'review_pending')
        self.assertEqual(done['remote_status'], 'completed')
        self.assertEqual([c[0] for c in self.stub.calls], ['/v1/submit', '/v1/get', '/v1/result'])
        self.assertIsNone(tick(self.store, self.bridge))

    def test_fair_rotation_with_running_oldest_reopen_and_equal_times(self):
        fake = self.store.create('fake', {})
        oldest = self.create()
        self.submit()
        newer_id = str(uuid.uuid4())
        newer = self.store.create('bridge-2', validate('bridge-2', 'review',
                                  '[PROJECT:hey-milo-dot] newer'), 'bridge')
        self.assertEqual(oldest['created_at'], newer['created_at'])
        self.stub.responses.append(response({'job_id': newer_id}))
        self.assertEqual(tick(self.store, self.bridge)['id'], newer['id'])
        for expected, remote_id in [(oldest, RID), (newer, newer_id)] * 3:
            self.store.close()
            self.store = Store(self.path, clock=lambda: self.now)
            self.stub.store = self.store
            self.stub.responses.append(response({'job_id': remote_id, 'status': 'running'}))
            polled = tick(self.store, self.bridge)
            self.assertEqual(polled['id'], expected['id'])
            self.assertEqual(polled['state'], 'remote_pending')
            self.assertEqual(self.stub.calls[-1][1], {'job_id': remote_id})
        self.assertEqual(self.store.get(fake['id'])['state'], 'queued')
        self.assertEqual(run_one(self.store)['id'], fake['id'])
        self.assertEqual([call[0] for call in self.stub.calls],
                         ['/v1/submit'] * 2 + ['/v1/get'] * 6)

    def test_result_findings_are_strings(self):
        for findings in [[{}], [1], [None], [True], ['valid', []], {}, 'text', None]:
            with self.subTest(findings=findings):
                self.stub.responses.append(response({'summary': 'done', 'findings': findings}))
                with self.assertRaises(BridgeError):
                    self.bridge.result(RID)
        for findings in [[], ['checked', '']]:
            self.stub.responses.append(response({'summary': 'done', 'findings': findings}))
            self.assertEqual(self.bridge.result(RID)['findings'], findings)

    def test_submit_timeout_and_crash_never_resubmit(self):
        self.create()
        self.stub.responses.append(TimeoutError('sensitive raw response'))
        self.assertEqual(tick(self.store, self.bridge)['state'], 'reconciliation_required')
        self.assertIsNone(tick(self.store, self.bridge))
        self.assertEqual(len(self.stub.calls), 1)

    def test_crash_after_acceptance_before_id_commit(self):
        job = self.create()
        self.stub.responses.append(response({'job_id': RID}))
        with patch.object(self.store, 'remote_update', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                tick(self.store, self.bridge, lease_seconds=1)
        self.now += 1
        self.assertIsNone(tick(self.store, self.bridge))
        self.assertEqual(self.store.get(job['id'])['state'], 'reconciliation_required')
        self.assertEqual(len(self.stub.calls), 1)

    def test_poll_timeout_unknown_status_and_result_retry_keep_id(self):
        self.create()
        self.submit()
        for read in [TimeoutError(), response({'job_id': RID, 'status': 'new_status'}),
                     response({'job_id': RID, 'status': []}), response({'job_id': str(uuid.uuid4()), 'status': 'completed'})]:
            self.stub.responses.append(read)
            job = tick(self.store, self.bridge)
            self.assertEqual(job['state'], 'remote_pending')
            self.assertEqual(job['remote_id'], RID)
        self.stub.responses.extend([response({'job_id': RID, 'status': 'completed'}), response({'summary': 3, 'findings': []})])
        self.assertEqual(tick(self.store, self.bridge)['state'], 'remote_pending')
        self.assertEqual(sum(c[0] == '/v1/submit' for c in self.stub.calls), 1)

    def test_terminal_failure(self):
        self.create()
        self.submit()
        self.stub.responses.append(response({'job_id': RID, 'status': 'timed_out'}))
        job = tick(self.store, self.bridge)
        self.assertEqual(job['state'], 'remote_failed')
        self.assertIsNone(job['result'])
        with self.assertRaises(ValueError):
            self.store.review(job['id'], 'approve', 'human', 'review')

    def test_poll_fencing_and_expired_submit(self):
        self.create()
        self.submit()
        old = self.store.claim('same', 1, 'bridge')
        self.now += 1
        new = self.store.claim('same', 10, 'bridge')
        self.assertGreater(new['fence'], old['fence'])
        with self.assertRaises(StaleClaim):
            self.store.remote_update(old['id'], 'same', old['fence'], status='failed')
        self.store.remote_update(new['id'], 'same', new['fence'], status='running')

    def test_payload_validation(self):
        for key, kind, task in [('bad key', 'research', '[PROJECT:x]'), ('x', 'cancel', '[PROJECT:x]'),
                                ('x', 'review', 'no marker'), ('x', 'research', '[PROJECT:x]' + 'a' * 12000)]:
            with self.assertRaises(ValueError):
                validate(key, kind, task)
        with self.assertRaises(ValueError):
            self.store.create('x', {'task': '[PROJECT:x]', 'kind': 'research', 'idempotency_key': 'x', 'extra': 1}, 'bridge')

    def test_malformed_redirect_oversize_and_redaction(self):
        for reply in [response({'job_id': 'bad'}), (302, b'secret'), (400, b'idempotency_conflict secret'),
                      (200, b'not json secret'), response([]), (200, b'x' * 1048577)]:
            self.stub.responses.append(reply)
            with self.assertRaises(BridgeError) as caught:
                self.bridge.submit(validate('x', 'review', '[PROJECT:x] test'))
            self.assertNotIn('secret', str(caught.exception))
            self.assertNotIn('dummy-test-token', str(caught.exception))

    def test_tls_config_and_external_paths(self):
        self.assertTrue(self.bridge.context.check_hostname)
        self.assertEqual(self.bridge.context.verify_mode, ssl.CERT_REQUIRED)
        for endpoint in ['http://bridge.invalid', 'https://user:pass@bridge.invalid', 'https://bridge.invalid/path']:
            with self.assertRaises(ValueError):
                Bridge(endpoint, lambda: 'dummy')
        with self.assertRaises(OSError):
            Bridge('https://bridge.invalid', lambda: 'dummy', ca_file='/nonexistent/milo-test-ca.pem')
        with patch('milo_dot.bridge.ssl.create_default_context', return_value=ssl.create_default_context()) as create:
            Bridge('https://bridge.invalid', lambda: 'dummy', ca_file='/external/test-ca.pem', transport=self.stub)
            create.assert_called_once_with(cafile='/external/test-ca.pem')
        for path in ['relative', str(Path.cwd() / 'token')]:
            with self.assertRaises(ValueError):
                external_path(path)

    def test_submit_response_after_lease_expiry_is_fenced(self):
        job = self.create()
        def late(path, body, headers):
            self.now += 60
            return response({'job_id': RID})
        self.bridge.transport = late
        with self.assertRaises(StaleClaim):
            tick(self.store, self.bridge)
        self.store.recover()
        self.assertEqual(self.store.get(job['id'])['state'], 'reconciliation_required')
        self.assertIsNone(self.store.get(job['id'])['remote_id'])

    def test_cli_create_and_tick_with_mock_config(self):
        from milo_dot.__main__ import main
        arguments = ['milo-dot', '--db', str(self.path), 'create-bridge', '--key', 'bridge-1',
                     '--kind', 'research', '--task', '[PROJECT:hey-milo-dot] test']
        with patch('sys.argv', arguments), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(main(), 0)
        self.stub.responses.append(response({'job_id': RID}))
        with patch('sys.argv', ['milo-dot', '--db', str(self.path), 'tick', '--config', '/external/mock-config.json']), \
             patch('pathlib.Path.read_text', return_value=json.dumps({'endpoint': 'https://bridge.invalid', 'token_file': '/external/mock-token'})), \
             patch('milo_dot.__main__.Bridge', return_value=self.bridge), \
             patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(main(), 0)
        self.assertEqual(self.store.list()[0]['remote_id'], RID)

    def test_phase2_migration_preserves_pending_remote(self):
        job = self.create()
        self.submit()
        # Hide the new scheduling column and remove the counter to exercise
        # version-2 migration while retaining the pending remote record.
        self.store.db.executescript("""
            ALTER TABLE jobs RENAME COLUMN claim_order TO old_claim_order;
            DROP TABLE claim_counter;
            PRAGMA user_version=2;
        """)
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.stub.store = self.store
        migrated = self.store.get(job['id'])
        self.assertEqual(migrated['remote_id'], RID)
        self.assertEqual(migrated['state'], 'remote_pending')
        self.stub.responses.append(response({'job_id': RID, 'status': 'running'}))
        self.assertEqual(tick(self.store, self.bridge)['id'], job['id'])
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 4)

    def test_phase1_migration(self):
        old = Path(self.temp.name) / 'phase1.db'
        db = sqlite3.connect(old)
        db.executescript('''CREATE TABLE jobs (
            id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL, state TEXT NOT NULL, fence INTEGER NOT NULL DEFAULT 0,
            owner TEXT, lease_until REAL, result TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
            INSERT INTO jobs VALUES ('old','old','{}','queued',7,NULL,NULL,NULL,1,1);''')
        db.close()
        migrated = Store(old)
        self.assertEqual(migrated.db.execute('PRAGMA user_version').fetchone()[0], 4)
        self.assertEqual(migrated.get('old')['adapter'], 'fake')
        self.assertEqual(migrated.get('old')['fence'], 7)
        self.assertIsNone(migrated.claim('bridge', adapter='bridge'))
        self.assertEqual(run_one(migrated)['state'], 'review_pending')
        migrated.close()
        migrated = Store(old)
        self.assertEqual(migrated.get('old')['result']['adapter'], 'fake-lex')
        migrated.close()
