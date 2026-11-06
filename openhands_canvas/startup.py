"""Supervisor entrypoint with body-blind upstream event normalization."""
import asyncio
import json
import os
from pathlib import Path
import pwd
import signal
import sys
from zoneinfo import ZoneInfo

from logging_utils import Logger, LOG_LEVELS


class CertificatePathError(ValueError):
    pass


def options(path='/data/options.json', ssl_root='/ssl'):
    with open(path, encoding='utf-8') as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or set(value) not in ({'log_level', 'timezone'}, {'log_level', 'timezone', 'certfile', 'keyfile'}):
        raise ValueError('invalid options')
    if value['log_level'] not in LOG_LEVELS:
        raise ValueError('invalid level')
    ZoneInfo(value['timezone'])
    # Legacy TLS options remain readable for existing installations. Native
    # Ingress itself uses Supervisor's HTTP transport and needs neither file.
    if 'certfile' in value:
        try:
            root = Path(ssl_root).resolve(strict=True)
            for name in ('certfile', 'keyfile'):
                relative = Path(value[name])
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('invalid certificate path')
                target = (root / relative).resolve(strict=True)
                if not target.is_relative_to(root) or not target.is_file():
                    raise ValueError('invalid certificate path')
                value[name] = str(target)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            raise CertificatePathError() from exc
    return value


def empty_tree(path):
    """Validate the complete tree before removing any directory; never follow links."""
    directories = []
    def visit(node):
        if node.is_symlink() or not node.is_dir():
            raise ValueError('legacy data conflict')
        for child in node.iterdir():
            visit(child)
        directories.append(node)
    visit(path)
    for directory in directories:
        directory.rmdir()  # Fails closed if anything appeared since validation.


def prepare(uid, gid, data='/data', legacy=('/home/openhands/.openhands', '/projects')):
    root = Path(data)
    # Only own newly created directories; never recursively chown existing state.
    for relative in ('home', 'home/.openhands', 'projects'):
        target = root / relative
        if target.is_symlink():
            raise ValueError('state symlink')
        if not target.exists():
            target.mkdir(mode=0o700)
            os.chown(target, uid, gid)
        if not target.is_dir() or target.stat().st_uid != uid:
            raise ValueError('state ownership conflict')
    for old, target in zip(legacy, (root / 'home/.openhands', root / 'projects')):
        old = Path(old)
        if old.is_symlink():
            if os.readlink(old) != str(target):
                raise ValueError('legacy symlink conflict')
        else:
            if old.exists():
                empty_tree(old)
            old.symlink_to(target, target_is_directory=True)


def environment(base):
    allowed = ('PATH', 'ACP_NODE_DIR', 'LC_ALL', 'LANG', 'OH_ACP_SKILL_SOURCING',
               'EDITOR', 'VISUAL', 'GIT_EDITOR',
               'OPENVSCODE_SERVER_ROOT', 'CHROME_BIN', 'PUPPETEER_EXECUTABLE_PATH',
               'CHROMIUM_FLAGS', 'LD_LIBRARY_PATH', 'AGENT_CANVAS_BASE_PATH',
               'OPENHANDS_BUILD_GIT_SHA', 'OPENHANDS_BUILD_GIT_REF',
               'AGENT_CANVAS_BUILD_GIT_SHA', 'AGENT_CANVAS_BUILD_GIT_REF')
    result = {key: base[key] for key in allowed if key in base}
    if 'LD_LIBRARY_PATH' in result:
        # Empty entries tell the loader to search the current working directory.
        result['LD_LIBRARY_PATH'] = ':'.join(
            part for part in result['LD_LIBRARY_PATH'].split(':') if part)
        if not result['LD_LIBRARY_PATH']:
            del result['LD_LIBRARY_PATH']
    result.update(HOME='/data/home', USER='openhands', LOGNAME='openhands',
                  LOG_JSON='true', AGENT_CANVAS_ALLOW_LAN_SESSION_KEY='false',
                  OH_ENABLE_VSCODE='false')
    return result


async def terminate(proc, grace=10):
    # Kill the entire session even when its leader has already exited.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), grace)
    except asyncio.TimeoutError:
        pass
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await proc.wait()


async def run(discovery, log, env):
    """Validate access prerequisites before entering the native runtime."""
    from native_ingress import SupervisorIngress
    if not isinstance(discovery, SupervisorIngress):
        raise ValueError('supervisor_discovery_required')
    # Revalidate even when run() is called independently of main().
    SupervisorIngress(discovery.prefix, discovery.origins)
    if (env.get('AGENT_CANVAS_ALLOW_LAN_SESSION_KEY') != 'false'
            or env.get('OH_ENABLE_VSCODE') != 'false'):
        raise ValueError('secure_runtime_environment_required')
    from native_runtime import serve
    await serve(discovery, log, env)


def main():
    log = Logger('startup')
    stage = 'options'
    try:
        cfg = options()
        log = Logger('startup', cfg['log_level'], cfg['timezone'])
        stage = 'account'
        account = pwd.getpwnam('openhands')
        if account.pw_uid <= 0 or account.pw_gid <= 0:
            raise ValueError('non-root uid and gid required')
        stage = 'supervisor_discovery'
        from native_ingress import SupervisorIngress
        token = os.environ.pop('SUPERVISOR_TOKEN', '')
        if not token.strip():
            stage = 'supervisor_token_required'
            raise ValueError('supervisor_token_required')
        discovery = asyncio.run(SupervisorIngress.discover(token))
        del token
        os.umask(0o077)
        stage = 'state_ownership_path'
        prepare(account.pw_uid, account.pw_gid)
        env = environment(os.environ)
        env['TZ'] = cfg['timezone']
        stage = 'privilege_drop'
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
        # Protect the launcher's original environment/heap (Supervisor token)
        # from same-uid tools via /proc and ptrace after dropping privileges.
        import ctypes
        if ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0) != 0:
            raise RuntimeError('process_privacy_failed')
        stage = 'working_directory'
        os.chdir('/data/home')
        stage = 'upstream_runtime'
        return asyncio.run(run(discovery, log, env))
    except Exception as exc:
        if isinstance(exc, CertificatePathError):
            stage = 'certificate_path'
        log.failure(stage, exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
