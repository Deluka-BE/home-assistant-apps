import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from milo_dot.pc import PCWake, HAError, ensure_pc_awake, SENSOR, BUTTON
from milo_dot.store import Store
from milo_dot.bridge import validate
from milo_dot.worker import tick


class PCTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'jobs.db'
        self.store = Store(self.path)
        self.now = 0
        self.ha = Mock()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def wait(self, seconds):
        self.now += seconds

    def ensure(self):
        return ensure_pc_awake(self.store, self.ha, timeout=8,
                               clock=lambda: self.now, wait=self.wait)

    def test_already_awake(self):
        self.ha.state.return_value = 'on'
        self.assertEqual(self.ensure(), {'ok': True, 'status': 'already_awake'})
        self.ha.wake.assert_not_called()

    def test_success_and_repeat(self):
        self.ha.state.side_effect = ['off', 'off', 'off', 'on', 'on']
        self.assertEqual(self.ensure()['status'], 'awake')
        self.assertEqual(self.ensure()['status'], 'already_awake')
        self.ha.wake.assert_called_once()

    def test_timeout_restart_no_repeat(self):
        self.ha.state.return_value = 'unavailable'
        self.assertEqual(self.ensure()['status'], 'timeout')
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.ensure()['status'], 'timeout')
        self.ha.wake.assert_called_once()
        self.assertEqual(self.now, 16)

    def test_read_error(self):
        self.ha.state.side_effect = HAError('private')
        self.assertEqual(self.ensure(), {'ok': False, 'status': 'ha_read_error'})
        self.ha.wake.assert_not_called()

    def test_wake_error_latched(self):
        self.ha.state.return_value = 'off'
        self.ha.wake.side_effect = HAError('private')
        self.assertEqual(self.ensure()['status'], 'wake_action_error')
        self.assertEqual(self.ensure()['status'], 'timeout')
        self.ha.wake.assert_called_once()

    def test_existing_reservation_and_recovery(self):
        with self.store.transaction():
            self.store.db.execute('INSERT INTO pc_wake_attempt VALUES(1)')
        self.ha.state.side_effect = ['off', 'on']
        self.assertTrue(self.ensure()['ok'])
        self.ha.wake.assert_not_called()
        self.ha.state.side_effect = ['off', 'off', 'on']
        self.assertTrue(self.ensure()['ok'])
        self.ha.wake.assert_called_once()

    def test_interruption(self):
        result = ensure_pc_awake(self.store, self.ha, stopped=lambda: True)
        self.assertEqual(result['status'], 'interrupted')
        self.ha.state.assert_not_called()

    def test_transport_only_fixed_entities(self):
        transport = Mock(side_effect=[(200, json.dumps({'entity_id': SENSOR, 'state': 'off'}).encode()),
                                     (200, b'[]')])
        client = PCWake('https://gateway.invalid', lambda: 'synthetic', transport=transport)
        self.assertEqual(client.state(2), 'off')
        client.wake(2)
        calls = transport.call_args_list
        self.assertEqual(calls[0].args[:3], ('GET', '/api/states/' + SENSOR, None))
        self.assertEqual(calls[1].args[:2], ('POST', '/api/services/button/press'))
        self.assertEqual(json.loads(calls[1].args[2]), {'entity_id': BUTTON})
        transport.side_effect = OSError('private')
        with self.assertRaisesRegex(HAError, '^HA request failed or response invalid$'):
            client.state(2)

    def test_worker_gate_failure_then_success(self):
        job = self.store.create('pc', validate('pc', 'research', '[PROJECT:hey-milo-dot] test'), 'bridge')
        bridge = Mock()
        self.ha.state.side_effect = HAError()
        result = tick(self.store, bridge, pc=self.ha)
        self.assertEqual(result['state'], 'queued')
        bridge.submit.assert_not_called()
        self.assertEqual(self.store.events(job['id'])[-1]['data']['status'], 'ha_read_error')
        self.ha.state.side_effect = None
        self.ha.state.return_value = 'on'
        bridge.submit.return_value = '11111111-1111-4111-8111-111111111111'
        self.assertEqual(tick(self.store, bridge, pc=self.ha)['state'], 'remote_pending')
        bridge.submit.assert_called_once()
        self.ha.state.side_effect = HAError()
        self.assertEqual(tick(self.store, bridge, pc=self.ha)['state'], 'remote_pending')
        bridge.get.assert_not_called()

    def test_arbitrary_writes_denied(self):
        transport = Mock()
        client = PCWake('https://gateway.invalid', lambda: 'synthetic', transport=transport)
        with self.assertRaisesRegex(HAError, 'capability denied'):
            client._request('POST', '/api/services/light/turn_on', b'{}', 2)
        transport.assert_not_called()

    def test_poll_read_error_keeps_reservation(self):
        self.ha.state.side_effect = ['off', 'off', HAError()]
        self.assertEqual(self.ensure()['status'], 'ha_read_error')
        self.ha.state.side_effect = None
        self.ha.state.return_value = 'off'
        self.assertEqual(self.ensure()['status'], 'timeout')
        self.ha.wake.assert_called_once()

    def test_competing_connection_sends_no_second_pulse(self):
        other = Store(self.path)
        try:
            with other.transaction():
                other.db.execute('INSERT INTO pc_wake_attempt VALUES(1)')
            self.ha.state.return_value = 'off'
            self.assertEqual(self.ensure()['status'], 'timeout')
            self.ha.wake.assert_not_called()
        finally:
            other.close()
