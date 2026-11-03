"""Independent userspace Funnel supervisor. Python standard library only."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request

DATA = Path("/data/tailscale")
SOCKET = DATA / "tailscaled.sock"
ORIGIN = "http://127.0.0.1:8000"
UID = 10002
LOG_ZONE = ZoneInfo("Europe/Brussels")
LOGIN_URL = re.compile(r"https://login\.tailscale\.com/a/[A-Za-z0-9]+")


def log(message: str) -> None:
    timestamp = datetime.now(LOG_ZONE).isoformat(timespec="seconds")
    print(f"{timestamp} MCP Funnel: {message}", flush=True)


def prepare(data: Path = DATA) -> None:
    """Only prepare our own directory; never read, remove or reset node state."""
    os.umask(0o077)
    if data.is_symlink():
        raise RuntimeError("unsafe data directory")
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.geteuid() == 0:
        os.chown(data, UID, UID)
        os.setgroups([])
        os.setgid(UID)
        os.setuid(UID)
        # The directory owner can chmod after dropping privileges, without FOWNER.
        # This also works on restart when persisted data already belongs to UID.
        os.chmod(data, 0o700)


def daemon_args(data: Path = DATA) -> list[str]:
    return ["tailscaled", "--tun=userspace-networking", "--port=0",
            f"--statedir={data.as_posix()}", f"--state={(data / 'tailscaled.state').as_posix()}",
            f"--socket={(data / 'tailscaled.sock').as_posix()}"]


def cli_args(*args: str, socket: Path = SOCKET) -> list[str]:
    return ["tailscale", f"--socket={socket.as_posix()}", *args]


def up_args(socket: Path = SOCKET) -> list[str]:
    return cli_args("up", "--json", "--hostname=mcp-funnel", "--accept-dns=false",
                    "--accept-routes=false", "--advertise-routes=",
                    "--advertise-exit-node=false", "--exit-node=",
                    "--netfilter-mode=off", socket=socket)


def settings_args(socket: Path = SOCKET) -> list[str]:
    # set changes only these preferences and cannot initiate enrollment.
    return cli_args("set", "--hostname=mcp-funnel", "--accept-dns=false",
                    "--accept-routes=false", "--advertise-routes=",
                    "--advertise-exit-node=false", "--exit-node=",
                    "--netfilter-mode=off", socket=socket)


class Diagnostics:
    """Fixed messages only, bounded per process lifetime; never echo raw output."""
    def __init__(self, startup_grace: bool = False):
        self.lock = threading.Lock()
        self.remaining = 200
        self.events = set()
        self.urls = set()
        self.startup_deadline = time.monotonic() + 30 if startup_grace else None
        self.pending_errors = {}
        self.startup_timed_out = False

    def finish_startup(self, ready: bool = False) -> None:
        with self.lock:
            if self.startup_deadline is None:
                return
            if not ready and time.monotonic() < self.startup_deadline:
                return
            pending = list(self.pending_errors.items()) if not ready else []
            self.startup_timed_out = not ready
            self.pending_errors.clear()
            self.startup_deadline = None
        for key, message in pending:
            self.event(key, "Tailscale readiness timeout (30 seconds); " + message)

    def event(self, key: str, message: str) -> None:
        with self.lock:
            if key in self.events or self.remaining <= 0:
                return
            self.events.add(key)
            self.remaining -= 1
            log(message)

    def auth_url(self, value, source: str) -> None:
        # source is an internal constant, never subprocess data.
        if not isinstance(value, str) or not value:
            return
        if not LOGIN_URL.fullmatch(value) or len(value) > 2048:
            self.event(source + "-url-rejected", source + ": AuthURL rejected by validation (value withheld).")
            return
        with self.lock:
            if value in self.urls or len(self.urls) >= 32 or self.remaining <= 0:
                return
            self.urls.add(value)
            self.remaining -= 1
            log(f"Authorize this new node in your browser: {value}")

    def error(self, text: str, source: str, required: bool = False) -> None:
        # Map arbitrary input to fixed categories. Redaction of arbitrary text is
        # insufficient: state, keys, identity, paths and URLs must never be echoed.
        categories = (("permission denied", "permission denied"),
                      ("operation not permitted", "permission denied"),
                      ("no such file", "file/socket unavailable"),
                      ("x509", "certificate validation"), ("tls", "TLS connection"),
                      ("lookup", "DNS resolution"), ("dns", "DNS resolution"),
                      ("timeout", "timeout"), ("connection refused", "connection refused"),
                      ("network is unreachable", "network unreachable"),
                      ("authentication", "authentication"), ("unauthorized", "authentication"),
                      ("flag provided but not defined", "unsupported CLI flag"))
        lowered = text.lower()
        category = next((label for marker, label in categories if marker in lowered), None)
        if category is None and (required or any(x in lowered for x in ("error", "failed", "fatal"))):
            category = "unclassified failure/output"
        if category:
            key = source + "-" + category
            message = source + ": " + category + " (raw output withheld)."
            with self.lock:
                if self.startup_deadline is not None:
                    # Retain only fixed categories, never subprocess text.
                    if len(self.pending_errors) < 32:
                        self.pending_errors[key] = message
                    return
            self.event(key, message)

    def status(self, status) -> None:
        states = {"NoState", "NeedsLogin", "NeedsMachineAuth", "Stopped", "Starting", "Running"}
        state = status.get("BackendState") if isinstance(status, dict) else None
        state = state if isinstance(state, str) and state in states else "unavailable/unknown"
        present = bool(status.get("AuthURL")) if isinstance(status, dict) else False
        self.event("status-" + state + str(present),
                   "Tailscale status: BackendState=" + state + "; AuthURL=" + ("present" if present else "absent") + ".")
        if present:
            self.auth_url(status["AuthURL"], "status fallback")


def read_login(stream, diagnostics=None) -> None:
    diagnostics = diagnostics if diagnostics is not None else Diagnostics()
    buffer = ""
    decoder = json.JSONDecoder()
    try:
        while True:
            line = stream.readline(4096)
            if not isinstance(line, str):
                raise TypeError
            if not line:
                diagnostics.event("stdout-eof", "Login stdout reader: EOF.")
                if buffer.strip():
                    diagnostics.event("stdout-incomplete", "Login stdout reader: incomplete/invalid JSON at EOF (withheld).")
                return
            diagnostics.event("stdout-data", "Login stdout reader: data received.")
            buffer += line
            if len(buffer) > 65536:
                buffer = ""
                diagnostics.event("stdout-limit", "Login stdout reader: buffer limit reached; content withheld.")
                continue
            while buffer.strip():
                buffer = buffer.lstrip()
                try:
                    item, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    diagnostics.event("stdout-await-json", "Login stdout reader: awaiting complete/valid JSON.")
                    break
                buffer = buffer[end:]
                diagnostics.event("stdout-json", "Login stdout reader: JSON received.")
                if isinstance(item, dict):
                    url = item.get("AuthURL")
                    diagnostics.event("stdout-url-" + str(bool(url)),
                                      "Login stdout reader: AuthURL " + ("found." if url else "absent."))
                    diagnostics.auth_url(url, "login stdout")
                    if item.get("Error"):
                        diagnostics.error(str(item["Error"]), "login JSON", required=True)
    except Exception:
        diagnostics.event("stdout-exception", "Login stdout reader: exception (details withheld).")


def read_errors(stream, diagnostics: Diagnostics, source: str, on_eof=None) -> None:
    try:
        while True:
            chunk = stream.readline(4096)
            if not isinstance(chunk, str):
                raise TypeError
            if not chunk:
                if on_eof is not None:
                    on_eof()
                else:
                    diagnostics.event(source + "-eof", source + " reader: EOF.")
                return
            diagnostics.error(chunk, source, required=source != "daemon")
    except Exception:
        diagnostics.event(source + "-reader-exception", source + " reader: exception (details withheld).")


def command_json(args: list[str], diagnostics: Diagnostics | None = None) -> dict | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=3,
                                check=False, stdin=subprocess.DEVNULL)
        if result.returncode:
            if diagnostics is not None:
                diagnostics.error(result.stderr, "CLI query", required=True)
            return None
        value = json.loads(result.stdout)
        # An absent ServeConfig may be serialized as null, not as an empty map.
        if value is None and args[-3:] == ["funnel", "status", "--json"]:
            return {}
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        if diagnostics is not None:
            diagnostics.error("timeout" if isinstance(exc, subprocess.TimeoutExpired)
                              else str(exc), "CLI query", required=True)
        return None


def connected(status: dict | None) -> bool:
    return bool(status and status.get("BackendState") == "Running"
                and isinstance(status.get("Self"), dict)
                and status["Self"].get("Online") is True)


def hostname(status: dict) -> str | None:
    value = status.get("Self", {}).get("DNSName")
    if not isinstance(value, str):
        return None
    value = value.rstrip(".")
    return value if re.fullmatch(r"[a-z0-9-]+\.[a-z0-9.-]+\.ts\.net", value) else None


def funnel_correct(config: dict | None, domain: str) -> bool:
    """Require only the intended persistent HTTPS root handler; reject extras."""
    if not isinstance(config, dict):
        return False
    hostport = f"{domain}:443"
    expected = {"TCP": {"443": {"HTTPS": True}},
                "Web": {hostport: {"Handlers": {"/": {"Proxy": ORIGIN}}}},
                "AllowFunnel": {hostport: True}}
    return all(config.get(key) == value for key, value in expected.items()) and all(
        key in expected or not value for key, value in config.items())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def gateway_ready(url: str = ORIGIN + "/health") -> bool:
    # No environment HTTP proxy, redirects or authentication involved.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(url, timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, ValueError):
        return False


def stop_process(process, timeout: float = 8) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


class Supervisor:
    def __init__(self, data: Path = DATA):
        self.data = data
        self.socket = data / "tailscaled.sock"
        self.stop = threading.Event()
        self.daemon = None
        self.daemon_reader = None
        self.closing = threading.Event()
        self.login = None
        self.writer = None
        self.writer_started = 0.0
        self.login_attempted = False
        self.next_settings = 0.0
        self.retry_delay = 1.0
        self.settings_ready = False
        self.last_message = None
        self.diagnostics = Diagnostics(startup_grace=True)

    def daemon_exit(self, code: int) -> None:
        self.diagnostics.event("daemon-exit", f"Tailscale daemon exited unexpectedly: exitcode={code}; state retained.")

    def daemon_eof(self) -> None:
        # Pipe EOF can precede process reaping. Give normal exit a bounded chance
        # to settle, then distinguish a still-live process. Never wait indefinitely.
        try:
            code = self.daemon.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            code = self.daemon.poll()
        if self.stop.is_set() or self.closing.is_set():
            self.diagnostics.event("daemon-eof-shutdown",
                                   "daemon stdout/stderr reader: EOF during expected shutdown/restart.")
        elif code is not None:
            self.daemon_exit(code)
        else:
            self.diagnostics.event("daemon-eof-alive",
                                   "daemon stdout/stderr reader: unexpected EOF while daemon is still alive; output diagnostics unavailable.")

    def notify(self, message: str) -> None:
        if message != self.last_message:
            log(message)
            self.last_message = message

    def start_login(self) -> None:
        self.login_attempted = True
        self.diagnostics.event("login-start", "start_login(): starting interactive Tailscale up (no auth-key).")
        self.login = subprocess.Popen(up_args(self.socket), stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                      stdin=subprocess.DEVNULL)
        pid = self.login.pid if isinstance(self.login.pid, int) else 0
        self.diagnostics.event(f"login-pid-{pid}", f"Login process started: PID={pid}.")
        threading.Thread(target=read_login, args=(self.login.stdout, self.diagnostics), daemon=True).start()
        threading.Thread(target=read_errors, args=(self.login.stderr, self.diagnostics, "login stderr"), daemon=True).start()

    def tick(self) -> None:
        code = self.daemon.poll()
        if code is not None:
            if not self.stop.is_set():
                self.daemon_exit(code)
                raise RuntimeError("Tailscale daemon stopped")
            return
        if self.writer is not None:
            if self.writer.poll() is None:
                if time.monotonic() - self.writer_started < 30:
                    return
                stop_process(self.writer, timeout=1)
                self.notify("Funnel configuration timed out; retrying without resetting state.")
            self.writer = None
        status = command_json(cli_args("status", "--json", socket=self.socket), self.diagnostics)
        self.diagnostics.finish_startup(ready=connected(status))
        self.diagnostics.status(status)
        if self.login is not None and self.login.poll() is not None:
            code = self.login.returncode
            pid = self.login.pid if isinstance(self.login.pid, int) else 0
            self.diagnostics.event(f"login-exit-{pid}", f"Login process exited: PID={pid}; exitcode={code}.")
            self.login = None
        # LocalAPI availability is not proof that enrollment is needed. Never
        # infer login from missing JSON, NoState, Starting, Stopped or DNS errors.
        if not connected(status):
            self.retry_delay = min(self.retry_delay * 2, 30)
            if status and status.get("BackendState") == "NeedsLogin":
                if self.login is None and not self.login_attempted:
                    self.start_login()
                self.notify("Tailscale login required; awaiting authorization (no automatic login retries).")
            elif status and status.get("BackendState") == "NeedsMachineAuth":
                self.notify("Waiting for Tailscale administrator machine approval; state retained.")
            else:
                if self.diagnostics.startup_deadline is not None:
                    self.notify("Tailscale not ready yet — retrying; existing state retained.")
                else:
                    if self.diagnostics.startup_timed_out:
                        self.diagnostics.event("readiness-timeout", "Tailscale readiness timeout (30 seconds); backend not ready; existing state retained.")
                    self.notify("Waiting for Tailscale readiness; existing state retained (retry capped at 30 seconds).")
            return
        self.retry_delay = 1.0
        if self.login is not None:
            return
        if not self.settings_ready:
            if time.monotonic() < self.next_settings:
                return
            try:
                result = subprocess.run(settings_args(self.socket), capture_output=True,
                                        text=True, timeout=3, check=False,
                                        stdin=subprocess.DEVNULL)
                self.settings_ready = result.returncode == 0
                if not self.settings_ready:
                    self.diagnostics.error(result.stderr, "settings CLI", required=True)
            except (OSError, subprocess.TimeoutExpired):
                self.diagnostics.event("settings-failure", "Settings CLI unavailable/timed out (details withheld).")
            if not self.settings_ready:
                self.next_settings = time.monotonic() + 30
                self.notify("Settings not ready; retrying without login or state reset.")
                return
        domain = hostname(status)
        if domain is None:
            self.notify("Tailscale hostname unavailable; not configuring Funnel.")
            return
        if not gateway_ready():
            self.notify("Waiting for Gateway HTTP 200 on 127.0.0.1:8000/health; retrying every 5 seconds.")
            return
        config = command_json(cli_args("funnel", "status", "--json", socket=self.socket))
        if config is None:
            self.notify("Cannot read Funnel configuration; retrying without changing state.")
            return
        if funnel_correct(config, domain):
            self.notify(f"Ready: https://{domain}/ -> {ORIGIN}")
            return
        if self.writer is None:
            self.writer = subprocess.Popen(
                cli_args("funnel", "--bg", "--yes", "--https=443", "--set-path=/",
                         ORIGIN, socket=self.socket), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            threading.Thread(target=read_errors, args=(self.writer.stderr, self.diagnostics, "Funnel CLI"), daemon=True).start()
            self.writer_started = time.monotonic()
        self.notify("Configuring persistent Funnel. If pending, check tailnet HTTPS/Funnel permission.")

    def run(self) -> int:
        try:
            self.daemon = subprocess.Popen(daemon_args(self.data), stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            self.daemon_reader = threading.Thread(
                target=read_errors,
                args=(self.daemon.stdout, self.diagnostics, "daemon", self.daemon_eof),
                daemon=True)
            self.daemon_reader.start()
            # A responsive LocalAPI proves liveness only. tick decides readiness
            # and authentication independently; transient failures never run up.
            while not self.stop.is_set():
                self.tick()
                delay = 5 if self.retry_delay == 1 else self.retry_delay
                if self.diagnostics.startup_deadline is not None:
                    delay = min(delay, max(0, self.diagnostics.startup_deadline - time.monotonic()))
                self.stop.wait(delay)
            return 0
        except (OSError, RuntimeError):
            log("Startup/daemon failure; state retained. Check add-on storage and binary availability.")
            return 1
        finally:
            # Set before terminating any child: teardown EOF is expected even
            # after another startup failure. Earlier exit diagnostics stay visible.
            self.closing.set()
            stop_process(self.writer, 1)
            stop_process(self.login, 1)
            if self.login is not None:
                pid = self.login.pid if isinstance(self.login.pid, int) else 0
                code = self.login.returncode if isinstance(self.login.returncode, int) else 0
                self.diagnostics.event(f"login-exit-{pid}", f"Login process exited: PID={pid}; exitcode={code} (shutdown).")
            stop_process(self.daemon, 15)
            if self.daemon_reader is not None:
                self.daemon_reader.join(timeout=1)


def main() -> int:
    supervisor = Supervisor()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda _signum, _frame: supervisor.stop.set())
    try:
        prepare()
    except (OSError, RuntimeError):
        log("Cannot prepare private persistent storage; no state was reset.")
        return 1
    return supervisor.run()


if __name__ == "__main__":
    raise SystemExit(main())
