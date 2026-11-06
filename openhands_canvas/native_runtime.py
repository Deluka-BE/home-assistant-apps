"""Lifecycle for native Ingress. Called after native access prerequisite checks."""
import asyncio
from contextlib import suppress
import aiohttp
import os
from pathlib import Path
import secrets
import signal
import sys

from aiohttp import web

from native_ingress import CanvasFiles, NativeCanvas
from upstream_logs import drain


def encryption_key(path):
    """Persist encryption material without following links or replacing state."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('encryption_key_permissions')
            value = stream.read(65)
        if len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('encryption_key_invalid')
        return value
    value = secrets.token_hex(32)
    with os.fdopen(fd, 'w') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    return value


async def serve(discovery, log, env):
    # Only loopback children; never invoke the upstream all-interface entrypoint.
    state = Path(env['HOME']) / '.openhands'
    key = secrets.token_hex(32)
    env = dict(env, OH_SESSION_API_KEYS_0=key, OH_ENABLE_VSCODE='false',
        OH_SECRET_KEY=encryption_key(state / 'ha-encryption-key'),
        OH_PERSISTENCE_DIR=str(state),
        OH_CONVERSATIONS_PATH=str(state / 'agent-canvas/conversations'),
        OH_BASH_EVENTS_DIR=str(state / 'agent-canvas/bash_events'),
        OH_EXTRA_PYTHON_PATH='/opt/agent-canvas/tools',
        EXTENSIONS_PATH='/opt/agent-canvas/node_modules/@openhands/extensions',
        OPENHANDS_SUPPRESS_BANNER='1', LOG_LEVEL='WARNING',
        OPENHANDS_AUTOMATION_API_KEY=key, AUTOMATION_LOCAL_API_KEY=key,
        AUTOMATION_AGENT_SERVER_API_KEY=key,
        AUTOMATION_AGENT_SERVER_URL='http://127.0.0.1:18000',
        AUTOMATION_KV_SECRET=encryption_key(state / 'ha-automation-key'),
        FILE_STORE='local', LOCAL_STORAGE_PATH=str(state / 'storage'),
        AUTOMATION_WORKSPACE_BASE=str(state / 'workspaces'),
        AUTOMATION_DB_URL='sqlite+aiosqlite:///' + str(state / 'automation.db'),
        AUTOMATION_BASE_URL='http://127.0.0.1:18001')
    files = CanvasFiles('/opt/agent-canvas/frontend')
    proxy = NativeCanvas(discovery, files, key)
    commands = [
        [sys.executable, '-m', 'openhands.agent_server', '--host', '127.0.0.1',
         '--port', '18000', '--import-modules', 'canvas_ui_tool'],
        [sys.executable, '-m', 'uvicorn', 'openhands.automation.app:app',
         '--host', '127.0.0.1', '--port', '18001', '--no-access-log',
         '--no-proxy-headers', '--log-level', 'warning'],
    ]
    children, tasks = [], []
    runner = web.AppRunner(proxy.app(), access_log=None, shutdown_timeout=10)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    try:
        for command in commands:
            proc = await asyncio.create_subprocess_exec(*command, env=env,
                start_new_session=True, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            children.append(proc)
            tasks.extend([asyncio.create_task(drain(proc.stdout, log, 'stdout')),
                          asyncio.create_task(drain(proc.stderr, log, 'stderr'))])
        # Authenticate a real API response before admitting a browser.
        async with aiohttp.ClientSession(trust_env=False) as session:
            for _ in range(90):
                if any(p.returncode is not None for p in children):
                    raise RuntimeError('backend_exited')
                try:
                    async with session.get('http://127.0.0.1:18000/api/settings',
                            headers={'X-Session-API-Key': key},
                            timeout=aiohttp.ClientTimeout(total=2)) as response:
                        if response.status == 200:
                            break
                except (OSError, aiohttp.ClientError, TimeoutError):
                    pass
                await asyncio.sleep(1)
            else:
                raise RuntimeError('backend_readiness_timeout')
        await runner.setup()
        await web.TCPSite(runner, '0.0.0.0', 8099).start()
        log.info('native_ingress_ready')
        waits = [asyncio.create_task(p.wait()) for p in children]
        waits.append(asyncio.create_task(stopped.wait()))
        tasks.extend(waits)
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await runner.cleanup()
        for proc in children:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for proc in children:
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
