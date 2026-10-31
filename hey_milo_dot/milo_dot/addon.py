"""Repository-local HA/container entrypoint; options never contain credentials."""
import json
import sys
from pathlib import Path

from .__main__ import main


def arguments(options):
    allowed = {'config', 'poll_interval', 'max_backoff', 'gwen_endpoint', 'gwen_timeout'}
    if not isinstance(options, dict) or set(options) - allowed or not options.get('config'):
        raise ValueError('explicit config required')
    result = ['milo-dot', '--db', '/data/state/jobs.sqlite3', 'serve', '--config', options['config']]
    for key in ('poll_interval', 'max_backoff', 'gwen_timeout'):
        if key in options:
            result += ['--' + key.replace('_', '-'), str(options[key])]
    if options.get('gwen_endpoint'):
        result += ['--gwen-endpoint', options['gwen_endpoint']]
    return result


def entrypoint():
    try:
        # Direct container use can supply the regular CLI arguments explicitly.
        if len(sys.argv) > 1:
            return main()
        sys.argv = arguments(json.loads(Path('/data/options.json').read_text()))
        return main()
    except (ValueError, TypeError, OSError):
        print('error: worker configuration unavailable or invalid', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(entrypoint())
