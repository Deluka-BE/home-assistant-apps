import http.client
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .demo import run_one
from .bridge import Bridge, validate
from .worker import tick
from .store import Store


def external_path(value):
    path = Path(value)
    repo = Path(__file__).resolve().parent.parent
    if not path.is_absolute() or path.resolve() == repo or repo in path.resolve().parents:
        raise ValueError('configuration files must be absolute paths outside repository')
    return path.resolve()


def main():
    parser = argparse.ArgumentParser(description='[PROJECT:hey-milo-dot] local orchestrator')
    default = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'milo-dot/jobs.sqlite3'
    parser.add_argument('--db', default=str(default))
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create')
    create.add_argument('--key', required=True)
    create.add_argument('--payload', required=True, type=json.loads)
    bridge_create = commands.add_parser('create-bridge')
    bridge_create.add_argument('--key', required=True)
    bridge_create.add_argument('--kind', choices=['research', 'review', 'implementation'], required=True)
    bridge_create.add_argument('--task', required=True)
    step = commands.add_parser('tick')
    step.add_argument('--config', required=True, help='external JSON file: endpoint, token_file, optional ca_file')
    serve_parser = commands.add_parser('serve')
    serve_parser.add_argument('--config', required=True)
    serve_parser.add_argument('--poll-interval', type=float, default=5)
    serve_parser.add_argument('--max-backoff', type=float, default=60)
    serve_parser.add_argument('--gwen-endpoint')
    serve_parser.add_argument('--gwen-timeout', type=float, default=30)
    for name in ('get', 'events'):
        commands.add_parser(name).add_argument('id')
    for name in ('list', 'recover', 'demo'):
        commands.add_parser(name)
    review = commands.add_parser('review')
    review.add_argument('id')
    review.add_argument('--decision', choices=['approve', 'reject'], required=True)
    review.add_argument('--reviewer', required=True)
    review.add_argument('--reason', required=True)
    reconcile = commands.add_parser('reconcile')
    reconcile.add_argument('id')
    reconcile.add_argument('--result', type=json.loads, required=True)
    reconcile.add_argument('--evidence', required=True)
    commands.add_parser('review-export').add_argument('id')
    imp = commands.add_parser('review-import')
    imp.add_argument('id')
    imp.add_argument('--file', required=True)
    gwen = commands.add_parser('review-gwen')
    gwen.add_argument('id')
    gwen.add_argument('--endpoint', required=True)
    gwen.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    store = None
    try:
        if args.command == 'serve':
            external_path(args.db)
        store = Store(args.db)
        if args.command in ('review-export', 'review-import', 'review-gwen'):
            from .gwen import export, read_json, run
            if args.command == 'review-export':
                result = export(store.get(args.id))
            elif args.command == 'review-import':
                result = store.import_gwen(args.id, read_json(args.file))
            else:
                result = store.import_gwen(args.id, run(export(store.get(args.id)), args.endpoint, args.timeout))
        elif args.command == 'create':
            result = store.create(args.key, args.payload)
        elif args.command == 'create-bridge':
            result = store.create(args.key, validate(args.key, args.kind, args.task), adapter='bridge')
        elif args.command in ('tick', 'serve'):
            config_path = external_path(args.config)
            config = json.loads(config_path.read_text())
            token_path = external_path(config['token_file'])
            ca_path = external_path(config['ca_file']) if config.get('ca_file') else None
            if args.command == 'serve':
                from .daemon import interval
                interval(args.poll_interval)
                interval(args.max_backoff)
                if not ca_path or not token_path.read_text().strip():
                    raise ValueError('CA and token required')
                if args.gwen_endpoint:
                    from urllib.parse import urlsplit
                    import math
                    url = urlsplit(args.gwen_endpoint)
                    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
                        raise ValueError('invalid Gwen endpoint')
                    if not math.isfinite(args.gwen_timeout) or not 0 < args.gwen_timeout <= 120:
                        raise ValueError('invalid Gwen timeout')
            bridge = Bridge(config['endpoint'], lambda: token_path.read_text().strip(),
                            ca_file=str(ca_path) if ca_path else None, timeout=config.get('timeout', 15))
            if args.command == 'serve':
                from .daemon import serve
                serve(store, bridge, args.poll_interval, args.max_backoff, args.gwen_endpoint, args.gwen_timeout)
                return 0
            result = tick(store, bridge)
        elif args.command in ('get', 'events'):
            result = getattr(store, args.command)(args.id)
        elif args.command in ('list', 'recover'):
            result = getattr(store, args.command)()
        elif args.command == 'demo':
            result = run_one(store)
        elif args.command == 'review':
            result = store.review(args.id, args.decision, args.reviewer, args.reason)
        else:
            result = store.reconcile(args.id, args.result, args.evidence)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, sqlite3.Error, OSError, TypeError, http.client.HTTPException) as error:
        print('error: operation failed; inspect configuration and job state', file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == '__main__':
    sys.exit(main())
