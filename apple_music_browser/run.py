"""Supervise the desktop and upstream browser/MCP as one container lifecycle."""
import os
from pathlib import Path
import signal
import subprocess
import time

os.umask(0o077)
for name in ("config", "browser", "cache"):
    Path("/data", name).mkdir(parents=True, exist_ok=True)
children = []

def stop(*_, exit_code=0):
    for child in reversed(children):
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
    raise SystemExit(exit_code)

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

def start(*args):
    children.append(subprocess.Popen(args))

start("Xvfb", ":0", "-screen", "0", "1280x800x24", "-nolisten", "tcp")
for _ in range(100):
    if Path("/tmp/.X11-unix/X0").exists():
        break
    time.sleep(0.1)
else:
    raise RuntimeError("X display failed to start")
start("openbox")
start("x11vnc", "-display", ":0", "-localhost", "-listen", "127.0.0.1", "-no6", "-forever", "-shared", "-nopw", "-rfbport", "5900")
start("websockify", "127.0.0.1:6080", "127.0.0.1:5900")
start("nginx", "-c", "/opt/addon/nginx.conf", "-g", "daemon off;")
start("/opt/venv/bin/python", "/opt/addon/server.py")
while all(child.poll() is None for child in children):
    time.sleep(1)
print("A required service exited; stopping container", flush=True)
stop(exit_code=1)
