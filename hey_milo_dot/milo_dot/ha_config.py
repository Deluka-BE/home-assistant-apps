"""Translate Supervisor options into private worker configuration."""
import json
import os
import ssl
from pathlib import Path
from urllib.parse import urlsplit


def provision(options, directory):
    endpoint = options.get('bridge_endpoint', '')
    token = options.get('bridge_token', '')
    certificate = options.get('bridge_ca_pem', '')
    u = urlsplit(endpoint)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment:
        raise ValueError('HTTPS Bridge origin required')
    if not isinstance(token, str) or not token.strip() or any(c in token for c in '\r\n'):
        raise ValueError('Bridge token required')
    if not isinstance(certificate, str) or not certificate.strip():
        raise ValueError('trusted certificate required')
    ssl.create_default_context(cadata=certificate)
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    contents = {'bridge-token': token.strip(), 'bridge-ca.pem': certificate}
    config = {'endpoint': endpoint, 'token_file': str(directory / 'bridge-token'), 'ca_file': str(directory / 'bridge-ca.pem')}
    if options.get('pc_wake_endpoint'):
        wake_endpoint = options['pc_wake_endpoint']
        u = urlsplit(wake_endpoint)
        if (u.scheme != 'https' or not u.hostname or u.username or u.password
                or u.path not in ('', '/') or u.query or u.fragment):
            raise ValueError('scoped HA gateway HTTPS origin required')
        wake_token = options.get('pc_wake_token', '')
        if not isinstance(wake_token, str) or not wake_token.strip() or any(c in wake_token for c in '\r\n'):
            raise ValueError('scoped HA gateway token required')
        wake_ca = options.get('pc_wake_ca_pem', '')
        if not isinstance(wake_ca, str) or not wake_ca.strip():
            raise ValueError('trusted gateway certificate required')
        ssl.create_default_context(cadata=wake_ca)
        contents.update({'pc-wake-token': wake_token.strip(), 'pc-wake-ca.pem': wake_ca})
        config['pc_wake'] = {'endpoint': wake_endpoint,
                             'token_file': str(directory / 'pc-wake-token'),
                             'ca_file': str(directory / 'pc-wake-ca.pem')}
    contents['bridge.json'] = json.dumps(config)
    for name, content in contents.items():
        path = directory / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(content)
    return {'config': str(directory / 'bridge.json'), **{key: options[key] for key in ('poll_interval', 'max_backoff', 'gwen_endpoint', 'gwen_timeout') if key in options}}
