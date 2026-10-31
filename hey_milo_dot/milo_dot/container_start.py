"""Initialize only the worker state directory, then permanently drop root."""
import os
import stat
import sys
from pathlib import Path


def prepare():
    state = Path('/data/state')
    if os.geteuid() == 0:
        try:
            state.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = state.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, 10001):
            raise ValueError('unsafe state directory')
        os.chown(state, 10001, 10001, follow_symlinks=False)
        os.chmod(state, 0o700)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    if os.geteuid() == 0:
        raise ValueError('worker must not run as root')
    os.umask(0o077)


def main():
    try:
        options = None
        if len(sys.argv) == 1:
            import json
            options = json.loads(Path('/data/options.json').read_text())
        prepare()
        if options is not None:
            from .ha_config import provision
            from .addon import arguments
            sys.argv = arguments(provision(options, '/data/state/config'))
    except (OSError, ValueError, TypeError, KeyError):
        print('error: worker state directory unavailable', file=sys.stderr)
        return 1
    from .addon import entrypoint
    return entrypoint()


if __name__ == '__main__':
    sys.exit(main())
