"""Bounded HTTPS Bridge client. No automatic retries or redirects."""
import http.client
import json
import math
import re
import ssl
import uuid
import time
from urllib.parse import urlsplit

STATUSES = {'queued', 'starting', 'running', 'cancellation_requested', 'recovering',
            'completed', 'cancelled', 'failed', 'interrupted', 'timed_out', 'quota_exhausted'}
FAILURES = STATUSES - {'queued', 'starting', 'running', 'cancellation_requested', 'recovering', 'completed'}


class BridgeError(ValueError):
    pass


def validate(key, kind, task):
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', key):
        raise ValueError('invalid Bridge key')
    if kind not in ('research', 'review', 'implementation'):
        raise ValueError('invalid Bridge kind')
    if not isinstance(task, str) or len(task) > 12000 or not re.match(r'^\[PROJECT:[A-Za-z0-9_.-]+\]', task):
        raise ValueError('task must start with project marker and be at most 12000 characters')
    return {'kind': kind, 'task': task, 'idempotency_key': key}


def job_id(value):
    if not isinstance(value, str):
        raise BridgeError('invalid Bridge job ID')
    try:
        uuid.UUID(value)
    except ValueError:
        raise BridgeError('invalid Bridge job ID') from None
    return value


class Bridge:
    def __init__(self, endpoint, token_provider, ca_file=None, timeout=15, max_bytes=1048576, transport=None):
        url = urlsplit(endpoint)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
            raise ValueError('Bridge endpoint must be an HTTPS origin')
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError('invalid timeout')
        if type(max_bytes) is not int or not 0 < max_bytes <= 10485760:
            raise ValueError('invalid response limit')
        self.context = ssl.create_default_context(cafile=ca_file)
        self.host, self.port = url.hostname, url.port
        self.provider, self.timeout, self.limit = token_provider, timeout, max_bytes
        self.transport = transport or self._https

    def _https(self, path, payload, headers):
        connection = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=self.context)
        deadline = time.monotonic() + self.timeout
        try:
            connection.request('POST', path, body=payload, headers=headers)
            if connection.sock is not None:
                connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
            response = connection.getresponse()
            data = bytearray()
            while len(data) <= self.limit:
                if response.isclosed():
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                # HTTPResponse owns the socket after a Connection: close response.
                sock = connection.sock or response.fp.raw._sock
                sock.settimeout(remaining)
                chunk = response.read1(min(8192, self.limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            return response.status, bytes(data)
        finally:
            connection.close()

    def _post(self, path, payload):
        try:
            token = self.provider()
            if not isinstance(token, str) or not token or any(c in token for c in '\r\n'):
                raise ValueError()
            status, body = self.transport(path, json.dumps(payload).encode(),
                                          {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
            if status != 200 or not isinstance(body, bytes) or len(body) > self.limit:
                raise ValueError()
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except Exception:
            raise BridgeError('Bridge request failed or response invalid') from None

    def submit(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'kind', 'task', 'idempotency_key'}:
            raise ValueError('invalid submit payload')
        validate(payload['idempotency_key'], payload['kind'], payload['task'])
        return job_id(self._post('/v1/submit', payload).get('job_id'))

    def get(self, remote_id):
        result = self._post('/v1/get', {'job_id': job_id(remote_id)})
        if result.get('job_id') != remote_id or not isinstance(result.get('status'), str) or result['status'] not in STATUSES:
            raise BridgeError('invalid Bridge status response')
        return result['status']

    def result(self, remote_id):
        result = self._post('/v1/result', {'job_id': job_id(remote_id)})
        if ('job_id' in result and result['job_id'] != remote_id) or not isinstance(result.get('summary'), str) or not isinstance(result.get('findings'), list) or not all(isinstance(item, str) for item in result['findings']):
            raise BridgeError('invalid Bridge result response')
        return {'summary': result['summary'], 'findings': result['findings']}
