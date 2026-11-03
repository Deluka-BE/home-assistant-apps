"""Fixed HA capability, intended for a gateway enforcing the same allowlist."""
import http.client
import json
import math
import ssl
import time
from urllib.parse import urlsplit

SENSOR = 'binary_sensor.pc_waker_pc_bereikbaar'
BUTTON = 'button.pc_wakker_maken'


class HAError(ValueError):
    pass


class PCWake:
    def __init__(self, endpoint, token_provider, ca_file=None, transport=None):
        url = urlsplit(endpoint)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.path not in ('', '/') or url.query or url.fragment):
            raise ValueError('scoped HA gateway HTTPS origin required')
        self.host, self.port = url.hostname, url.port
        self.context = ssl.create_default_context(cafile=ca_file)
        self.provider = token_provider
        self.transport = transport or self._https

    def _https(self, method, path, body, headers, timeout):
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=self.context)
        deadline = time.monotonic() + timeout
        try:
            conn.request(method, path, body=body, headers=headers)
            if conn.sock:
                conn.sock.settimeout(max(.001, deadline - time.monotonic()))
            response = conn.getresponse()
            data = bytearray()
            while not response.isclosed():
                remaining = deadline - time.monotonic()
                if remaining <= 0 or len(data) > 65536:
                    raise HAError()
                sock = conn.sock or response.fp.raw._sock
                sock.settimeout(remaining)
                chunk = response.read1(min(8192, 65537 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > 65536:
                raise HAError()
            return response.status, bytes(data)
        finally:
            conn.close()

    def _request(self, method, path, body, timeout):
        allowed_read = method == 'GET' and path == '/api/states/' + SENSOR and body is None
        allowed_write = (method == 'POST' and path == '/api/services/button/press'
                         and body == json.dumps({'entity_id': BUTTON}).encode())
        if not (allowed_read or allowed_write):
            raise HAError('HA capability denied')
        try:
            token = self.provider()
            if not isinstance(token, str) or not token or any(c in token for c in '\r\n'):
                raise HAError()
            status, data = self.transport(method, path, body, {
                'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, timeout)
            if status != 200 or not isinstance(data, bytes) or len(data) > 65536:
                raise HAError()
            return json.loads(data)
        except Exception:
            raise HAError('HA request failed or response invalid') from None

    def state(self, timeout):
        value = self._request('GET', '/api/states/' + SENSOR, None, timeout)
        if (not isinstance(value, dict) or value.get('entity_id') != SENSOR
                or value.get('state') not in ('on', 'off', 'unavailable')):
            raise HAError('invalid PC state')
        return value['state']

    def wake(self, timeout):
        self._request('POST', '/api/services/button/press',
                      json.dumps({'entity_id': BUTTON}).encode(), timeout)


def ensure_pc_awake(store, ha, timeout=90, stopped=lambda: False,
                    clock=time.monotonic, wait=time.sleep):
    """Reserve before writes. Ambiguous/failed wake attempts never auto-repeat.

    A later observed 'on' releases the latch; otherwise explicit operator
    reconciliation is required before another pulse can be attempted.
    """
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError('wake timeout must be 0..120 seconds')
    deadline = clock() + timeout
    attempted = False

    def result(status):
        return {'ok': status in ('already_awake', 'awake'), 'status': status}

    def read():
        return ha.state(max(.001, min(10, deadline - clock())))

    if stopped():
        return result('interrupted')
    try:
        state = read()
    except HAError:
        return result('ha_read_error')
    if state == 'on':
        with store.transaction():
            store.db.execute('DELETE FROM pc_wake_attempt')
        return result('already_awake')
    with store.transaction():
        attempted = bool(store.db.execute(
            'INSERT OR IGNORE INTO pc_wake_attempt VALUES(1)').rowcount)
    if attempted:
        # Recheck after the atomic reservation, including competing callers.
        try:
            state = read()
        except HAError:
            return result('ha_read_error')
        if state != 'on':
            if stopped():
                return result('interrupted')
            if clock() >= deadline:
                return result('timeout')
            try:
                ha.wake(min(10, deadline - clock()))
            except HAError:
                return result('wake_action_error')
    delay = 1
    while state != 'on':
        if stopped():
            return result('interrupted')
        remaining = deadline - clock()
        if remaining <= 0:
            return result('timeout')
        wait(min(delay, remaining))
        if stopped():
            return result('interrupted')
        if clock() >= deadline:
            return result('timeout')
        try:
            state = read()
        except HAError:
            return result('ha_read_error')
        delay = min(delay * 2, 5)
    with store.transaction():
        store.db.execute('DELETE FROM pc_wake_attempt')
    return result('awake')
