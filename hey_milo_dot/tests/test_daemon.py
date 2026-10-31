import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from milo_dot.bridge import BridgeError, validate
from milo_dot.daemon import serve, review_one
from milo_dot.store import Store

RID = '11111111-1111-4111-8111-111111111111'


class Waiter:
    def __init__(self, count):
        self.delays = []
        self.count = count
    def is_set(self):
        return len(self.delays) >= self.count
    def wait(self, delay):
        self.delays.append(delay)


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'jobs.db'
        self.store = Store(self.path)
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def create(self, key='test'):
        return self.store.create(key, validate(key, 'research', '[PROJECT:hey-milo-dot] test'), 'bridge')

    def test_pacing_backoff_reset_and_cap(self):
        stop = Waiter(5)
        def step(*args, **kwargs):
            if len(stop.delays) < 4:
                kwargs['on_error']()
        with patch('milo_dot.daemon.tick', side_effect=step):
            serve(self.store, None, 1, 5, stop=stop, install_signals=False)
        self.assertEqual(stop.delays, [2, 4, 5, 5, 1])
        stop = Waiter(3)
        serve(self.store, None, 1, 5, stop=stop, install_signals=False)
        self.assertEqual(stop.delays, [1, 1, 1])

    def test_uncertain_and_expired_dispatch_never_resend(self):
        self.create()
        class Adapter:
            calls = 0
            def submit(self, payload):
                self.calls += 1
                raise BridgeError('secret')
        adapter = Adapter()
        serve(self.store, adapter, 1, 5, stop=Waiter(3), install_signals=False)
        self.assertEqual(adapter.calls, 1)
        job = self.create('expired')
        claim = self.store.claim('crashed', 1, 'bridge')
        self.store.begin_dispatch(claim['id'], 'crashed', claim['fence'])
        self.store.db.execute('UPDATE jobs SET lease_until=0 WHERE id=?', (job['id'],))
        serve(self.store, adapter, 1, 5, stop=Waiter(2), install_signals=False)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.store.get(job['id'])['state'], 'reconciliation_required')

    def test_gwen_advisory_dedup_failure_restart_fake_exclusion(self):
        job = self.create()
        claim = self.store.claim('test', adapter='bridge')
        self.store.begin_dispatch(claim['id'], 'test', claim['fence'])
        self.store.finish(claim['id'], 'test', claim['fence'], {'summary': 'done'})
        fake = self.store.create('fake', {'task': 'fake'})
        claim = self.store.claim('test')
        self.store.begin_dispatch(claim['id'], 'test', claim['fence'])
        self.store.finish(claim['id'], 'test', claim['fence'], {})
        def response(bundle, *args):
            return {k: bundle[k] for k in ('version', 'job_id', 'result_hash')} | {'response': {
                'model': 'qwen3:8b', 'done': True,
                'response': json.dumps({'verdict': 'no_findings', 'findings': []})}}
        with patch('milo_dot.daemon.run', side_effect=response) as call:
            for _ in range(3):
                review_one(self.store, 'http://stub', 1, lambda: False)
            self.assertEqual(call.call_count, 1)
            changes = self.store.db.total_changes
            review_one(self.store, 'http://stub', 1, lambda: False)
            self.assertEqual(self.store.db.total_changes, changes)
        self.assertEqual(self.store.get(job['id'])['state'], 'review_pending')
        self.assertEqual(self.store.get(fake['id'])['state'], 'review_pending')
        self.store.db.execute('DELETE FROM gwen_attempts')
        self.store.db.execute('DELETE FROM gwen_reviews')
        with patch('milo_dot.daemon.run', side_effect=OSError('secret')) as call:
            review_one(self.store, 'http://stub', 1, lambda: False)
            self.store.close()
            self.store = Store(self.path)
            review_one(self.store, 'http://stub', 1, lambda: False)
            self.assertEqual(call.call_count, 1)

    def test_real_subprocess_sigterm_restart_remote_id(self):
        self.create()
        # Fake adapter in a real child process; no external network or service.
        code = '''
import sys, time
from pathlib import Path
from milo_dot.store import Store
from milo_dot.daemon import serve
class Adapter:
    def submit(self, payload):
        with open(sys.argv[2], 'a') as f: f.write('submit\\n')
        Path(sys.argv[3]).touch()
        time.sleep(0.3)
        return '11111111-1111-4111-8111-111111111111'
    def get(self, remote):
        with open(sys.argv[2], 'a') as f: f.write('get:' + remote + '\\n')
        Path(sys.argv[3]).touch()
        time.sleep(0.3)
        return 'completed'
    def result(self, remote):
        raise AssertionError('must stop after current request')
s = Store(sys.argv[1])
try: serve(s, Adapter(), .05, 1)
finally: s.close()
'''
        calls = Path(self.tmp.name) / 'calls'
        ready = Path(self.tmp.name) / 'ready'
        for iteration in range(4):
            ready.unlink(missing_ok=True)
            process = subprocess.Popen([sys.executable, '-c', code, str(self.path), str(calls), str(ready)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists())
                process.send_signal(signal.SIGTERM if iteration % 2 == 0 else signal.SIGINT)
                out, err = process.communicate(timeout=3)
                self.assertEqual(process.returncode, 0, err)
                self.assertEqual((out, err), (b'', b''))
                self.assertEqual(self.store.list()[0]['remote_id'], RID)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        self.assertEqual(calls.read_text().splitlines(), ['submit'] + ['get:' + RID] * 3)
        self.assertEqual(self.store.list()[0]['state'], 'remote_pending')

    def test_invalid_intervals(self):
        for value in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                serve(self.store, None, value, install_signals=False)

    def test_cli_fail_closed_and_packaging_options(self):
        from milo_dot.addon import arguments
        with self.assertRaises(ValueError):
            arguments({})
        args = arguments({'config': '/data/bridge.json', 'gwen_endpoint': ''})
        self.assertNotIn('--gwen-endpoint', args)
        self.assertEqual(args[1:4], ['--db', '/data/state/jobs.sqlite3', 'serve'])
        config = Path(self.tmp.name) / 'config.json'
        for value in ({}, {'endpoint': 'https://example.invalid',
                            'token_file': str(Path(self.tmp.name) / 'missing-token')},
                      {'endpoint': 'https://example.invalid',
                       'token_file': str(config), 'ca_file': '/missing-ca'}):
            config.write_text(json.dumps(value))
            proc = subprocess.run([sys.executable, '-m', 'milo_dot', '--db', str(self.path),
                                   'serve', '--config', str(config)], capture_output=True, timeout=5)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, b'')
            self.assertEqual(proc.stderr, b'error: operation failed; inspect configuration and job state\n')
