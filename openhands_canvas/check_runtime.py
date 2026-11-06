"""Build-time checks for the rootfs copied from the pinned upstream image.

Run with the final image's Python. This does not start upstream services.
"""
import os
from pathlib import Path
import stat


def _can_execute(info, uid, gid):
    # The wrapper clears supplementary groups before dropping privileges.
    bit = (stat.S_IXUSR if info.st_uid == uid else
           stat.S_IXGRP if info.st_gid == gid else stat.S_IXOTH)
    return bool(info.st_mode & bit)


def _check_executable(root, name, uid, gid, require_read=False):
    # Resolve links within the image root, checking search permission along
    # every traversed path (including directories before a symlink or '..').
    pending = list(Path(name).parts)
    current = root
    links = 0
    while pending:
        info = current.stat()
        if not stat.S_ISDIR(info.st_mode) or not _can_execute(info, uid, gid):
            raise ValueError('directory not searchable by openhands: ' + str(current))
        part = pending.pop(0)
        if part == '.':
            continue
        if part == '..':
            current = current.parent if current != root else root
            continue
        candidate = current / part
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            links += 1
            if links > 40:
                raise ValueError('too many executable symlinks: /' + name)
            target = Path(os.readlink(candidate))
            if target.is_absolute():
                current = root
                target = target.relative_to('/')
            pending = list(target.parts) + pending
        else:
            current = candidate
    info = current.stat()
    if not stat.S_ISREG(info.st_mode) or not _can_execute(info, uid, gid):
        raise ValueError('required executable missing or not executable by openhands: /' + name)
    if require_read:
        bit = (stat.S_IRUSR if info.st_uid == uid else
               stat.S_IRGRP if info.st_gid == gid else stat.S_IROTH)
        if not info.st_mode & bit:
            raise ValueError('required script not readable by openhands: /' + name)


def check(root=Path('/')):
    root = Path(root).absolute()
    accounts = [line.split(':') for line in
                (root / 'etc/passwd').read_text().splitlines()
                if line.startswith('openhands:')]
    if len(accounts) != 1 or len(accounts[0]) != 7:
        raise ValueError('exactly one valid openhands account required')
    uid, gid = map(int, accounts[0][2:4])
    if uid <= 0 or gid <= 0:
        raise ValueError('non-root openhands uid and gid required')
    for name in ('usr/bin/tini', 'usr/local/bin/python3',
                 'opt/agent-canvas/entrypoint.sh'):
        try:
            _check_executable(root, name, uid, gid,
                              require_read=name == 'opt/agent-canvas/entrypoint.sh')
        except OSError as exc:
            raise ValueError('required executable unavailable: /' + name) from exc


if __name__ == '__main__':
    check()
    print('Canvas upstream runtime prerequisites verified')
