"""Build-time exact-input suppression of the pinned upstream HTML producers.

Defense in depth: the native frontend reads files directly and never runs this
static server. Unknown image sources fail the build rather than guessing a patch.
"""
import hashlib
from pathlib import Path

STATIC_SHA256 = '4a09c7fc9aa243256fac92ad9787bd0ae0ef788501d92f1d2dbc80ad34473a67'


def sanitize_static_server(source):
    if hashlib.sha256(source).hexdigest() != STATIC_SHA256:
        raise ValueError('unrecognized_static_server')
    start = source.index(b'  const parts = [];', source.index(b'function makeConfigInjectionScript('))
    end = source.index(b'\n}\n', start)
    # Replace the whole producer: no key, runtime-services, editor URL or cloud
    # metadata reaches HTML, even if upstream flags/environment contain secrets.
    return source[:start] + b'  return ""; // HA: runtime injection is forbidden.\n' + source[end:]


def prepare(root=Path('/')):
    target = root / 'opt/agent-canvas/scripts/static-server.mjs'
    original = target.read_bytes()
    sanitized = sanitize_static_server(original)
    target.write_bytes(sanitized)


if __name__ == '__main__':
    prepare()
