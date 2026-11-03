import contextlib
import copy
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "mcp_funnel"
spec = importlib.util.spec_from_file_location("funnel_runtime", ADDON / "runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
DOMAIN = "mcp-funnel.test-tail.ts.net"
STATUS = {"BackendState": "Running", "Self": {"Online": True, "DNSName": DOMAIN + "."}}
CONFIG = {"TCP": {"443": {"HTTPS": True}},
          "Web": {DOMAIN + ":443": {"Handlers": {"/": {"Proxy": runtime.ORIGIN}}}},
          "AllowFunnel": {DOMAIN + ":443": True}}


class FunnelTests(unittest.TestCase):
    def test_log_prefixes_each_message_with_current_brussels_timestamp(self):
        messages = ["Waiting for Gateway", "Ready: https://example.ts.net"]
        before = datetime.now(timezone.utc).replace(microsecond=0)
        with patch("builtins.print") as output:
            for message in messages:
                runtime.log(message)
        after = datetime.now(timezone.utc)
        self.assertEqual(output.call_count, len(messages))
        for call, message in zip(output.call_args_list, messages):
            line, = call.args
            timestamp, text = line.split(" ", 1)
            parsed = datetime.fromisoformat(timestamp)
            self.assertEqual(parsed.utcoffset(), parsed.astimezone(runtime.LOG_ZONE).utcoffset())
            self.assertLessEqual(before, parsed)
            self.assertLessEqual(parsed, after)
            self.assertEqual(text, f"MCP Funnel: {message}")
            self.assertEqual(call.kwargs, {"flush": True})

    def test_brussels_winter_summer_and_all_log_routes(self):
        for month, offset in ((1, "+01:00"), (7, "+02:00")):
            instant = datetime(2026, month, 15, 12, tzinfo=timezone.utc)
            with self.subTest(month=month), patch.object(runtime, "datetime") as clock, \
                 patch("builtins.print") as output:
                clock.now.return_value = instant.astimezone(runtime.LOG_ZONE)
                value = self.supervisor()
                runtime.log("direct")
                value.notify("state transition")
                value.diagnostics.event("event", "diagnostic")
                value.diagnostics.error("TLS error PRIVATE-SECRET", "daemon")
                value.diagnostics.finish_startup()
                value.diagnostics.auth_url("https://login.tailscale.com/a/fake123", "login stdout")
                runtime.read_login(io.StringIO(""), value.diagnostics)
                runtime.read_errors(io.StringIO(""), value.diagnostics, "Funnel CLI")
                value.daemon.wait.return_value = 7
                value.daemon_eof()
                for call in output.call_args_list:
                    stamp = call.args[0].split(" ", 1)[0]
                    self.assertTrue(stamp.endswith(offset), stamp)
                    self.assertNotIn("PRIVATE-SECRET", call.args[0])
                clock.now.assert_called_with(runtime.LOG_ZONE)
        # All runtime output, including exception paths, must use the formatter.
        import ast
        tree = ast.parse((ADDON / "runtime.py").read_text())
        printers = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "print"]
        self.assertEqual(len(printers), 1)
        self.assertIn("--no-install-recommends tzdata", (ADDON / "Dockerfile").read_text())

    def test_daemon_eof_shutdown_and_restart_are_expected(self):
        for flag in ("stop", "closing"):
            value = self.supervisor()
            getattr(value, flag).set()
            value.daemon.wait.return_value = -15
            with patch.object(runtime, "log") as output:
                value.daemon_eof()
                value.daemon_eof()
            output.assert_called_once_with(
                "daemon stdout/stderr reader: EOF during expected shutdown/restart.")

    def test_daemon_exit_codes_and_reader_tick_race_deduplicate(self):
        for code in (0, 7, -9):
            for reader_first in (True, False):
                value = self.supervisor()
                value.daemon.wait.return_value = code
                value.daemon.poll.return_value = code
                with patch.object(runtime, "log") as output:
                    if reader_first:
                        value.daemon_eof()
                    with self.assertRaises(RuntimeError):
                        value.tick()
                    value.daemon_eof()
                output.assert_called_once_with(
                    f"Tailscale daemon exited unexpectedly: exitcode={code}; state retained.")

    def test_daemon_pipe_closure_alive_then_exit_remains_visible(self):
        value = self.supervisor()
        value.daemon.wait.side_effect = subprocess.TimeoutExpired("private", 0.2)
        with patch.object(runtime, "log") as output:
            runtime.read_errors(io.StringIO(""), value.diagnostics, "daemon", value.daemon_eof)
            value.daemon_eof()
            value.daemon.poll.return_value = 9
            with self.assertRaises(RuntimeError):
                value.tick()
        self.assertEqual(output.call_count, 2)
        self.assertIn("unexpected EOF while daemon is still alive", output.call_args_list[0].args[0])
        self.assertIn("exitcode=9", output.call_args_list[1].args[0])
        value.daemon.wait.assert_called_with(timeout=0.2)

    def test_real_daemon_pipes_exit_and_live_closure(self):
        fixtures = (("import sys; sys.exit(6)", False),
                    ("import os,time; os.close(1); os.close(2); time.sleep(10)", True))
        for script, alive in fixtures:
            value = self.supervisor()
            with subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True) as process:
                value.daemon = process
                try:
                    with patch.object(runtime, "log") as output:
                        runtime.read_errors(process.stdout, value.diagnostics, "daemon", value.daemon_eof)
                    self.assertIn("still alive" if alive else "exitcode=6", output.call_args.args[0])
                    self.assertEqual(process.poll() is None, alive)
                finally:
                    value.closing.set()
                    runtime.stop_process(process, 1)

    def test_eof_exit_and_shutdown_during_bounded_wait(self):
        for shutdown in (True, False):
            value = self.supervisor()
            def settle(timeout):
                if shutdown:
                    value.stop.set()
                value.daemon.poll.return_value = 4
                raise subprocess.TimeoutExpired("PRIVATE-SECRET", timeout)
            value.daemon.wait.side_effect = settle
            with patch.object(runtime, "log") as output:
                value.daemon_eof()
            text = output.call_args.args[0]
            self.assertIn("expected shutdown/restart" if shutdown else "exitcode=4", text)
            self.assertNotIn("still alive", text)
            self.assertNotIn("PRIVATE-SECRET", text)

    def supervisor(self):
        value = runtime.Supervisor()
        value.daemon = Mock()
        value.daemon.poll.return_value = None
        value.settings_ready = True
        return value

    def test_manifest_security_and_independence(self):
        config = yaml.safe_load((ADDON / "config.yaml").read_text())
        self.assertTrue(config["host_network"])
        self.assertEqual(config["arch"], ["amd64", "aarch64"])
        for name in ("hassio_api", "homeassistant_api", "auth_api", "docker_api", "full_access", "host_dbus", "host_pid"):
            self.assertFalse(config[name])
        for name in ("privileged", "devices", "map"):
            self.assertEqual(config[name], [])
        self.assertTrue(config["apparmor"])
        self.assertEqual(config["boot"], "auto")
        self.assertEqual(config["timeout"], 30)
        self.assertEqual(config["options"], {})
        self.assertNotIn("ports", config)

    def test_pinned_base_multiarch_and_narrow_context(self):
        text = (ADDON / "Dockerfile").read_text()
        self.assertEqual(text.count("@sha256:"), 2)
        self.assertIn("v1.102.4", text)
        self.assertIn('ENTRYPOINT ["python", "/opt/mcp-funnel/runtime.py"]', text)
        self.assertNotIn("hevy_mcp", text)
        self.assertEqual((ADDON / ".dockerignore").read_text().splitlines(),
                         ["*", "!Dockerfile", "!runtime.py", "!healthcheck.py"])

    def test_daemon_userspace_auto_udp_and_private_data(self):
        args = runtime.daemon_args()
        self.assertIn("--tun=userspace-networking", args)
        self.assertIn("--port=0", args)
        self.assertIn("--socket=/data/tailscale/tailscaled.sock", args)
        self.assertIn("--state=/data/tailscale/tailscaled.state", args)
        self.assertNotIn("/dev/net/tun", " ".join(args))
        args = runtime.up_args()
        for flag in ("--accept-dns=false", "--accept-routes=false", "--advertise-routes=",
                     "--advertise-exit-node=false", "--exit-node=", "--netfilter-mode=off"):
            self.assertIn(flag, args)
        self.assertNotIn("--auth-key", " ".join(args))

    def test_existing_state_is_untouched_and_privileges_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "tailscale"
            data.mkdir()
            state = data / "tailscaled.state"
            state.write_bytes(b"fake-state-secret")
            with patch.object(runtime.os, "geteuid", return_value=0, create=True), \
                 patch.object(runtime.os, "chown", create=True) as chown, \
                 patch.object(runtime.os, "chmod") as chmod, \
                 patch.object(runtime.os, "setgroups", create=True) as groups, \
                 patch.object(runtime.os, "setgid", create=True) as gid, \
                 patch.object(runtime.os, "setuid", create=True) as uid:
                order = []
                for name, operation in (("chown", chown), ("setgroups", groups),
                                        ("setgid", gid), ("setuid", uid), ("chmod", chmod)):
                    operation.side_effect = lambda *_args, label=name: order.append(label)
                runtime.prepare(data)
                self.assertEqual(order, ["chown", "setgroups", "setgid", "setuid", "chmod"])
                self.assertEqual(state.read_bytes(), b"fake-state-secret")
                chown.assert_called_once_with(data, 10002, 10002)
                chmod.assert_called_once_with(data, 0o700)
                groups.assert_called_once_with([])
                gid.assert_called_once_with(10002)
                uid.assert_called_once_with(10002)

    def test_login_log_only_allows_official_url(self):
        values = [{"AuthURL": "https://login.tailscale.com/a/fake123", "secret": "fake-state-secret"},
                  {"AuthURL": "https://evil.example/a/fake-state-secret"},
                  {"Error": "fake-state-secret"}, {"BackendState": "Running"}]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            runtime.read_login(io.StringIO("\n".join(json.dumps(v, indent=2) for v in values)))
        self.assertIn("https://login.tailscale.com/a/fake123", output.getvalue())
        self.assertNotIn("fake-state-secret", output.getvalue())
        self.assertNotIn("evil.example", output.getvalue())

    def test_gateway_offline_then_online_without_relogin(self):
        value = self.supervisor()
        with patch.object(runtime, "command_json", side_effect=[STATUS, STATUS, {}]), \
             patch.object(runtime, "gateway_ready", side_effect=[False, True]), \
             patch.object(runtime.subprocess, "Popen") as popen:
            value.tick()
            popen.assert_not_called()
            value.tick()
            args = popen.call_args.args[0]
            self.assertIn("funnel", args)
            self.assertIn("--bg", args)
            self.assertIn("--https=443", args)
            self.assertIn(runtime.ORIGIN, args)
            self.assertNotIn("up", args)

    def test_status_fallback_and_stdout_share_deduplication_without_flow_change(self):
        value = self.supervisor()
        value.settings_ready = False
        value.login = Mock(pid=123)
        value.login.poll.return_value = None
        url = "https://login.tailscale.com/a/fake123"
        status = {"BackendState": "NeedsLogin", "AuthURL": url, "PrivateKey": "PRIVATE-SECRET"}
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             patch.object(runtime, "command_json", return_value=status), \
             patch.object(value, "start_login") as start, \
             patch.object(runtime.subprocess, "Popen") as popen:
            value.tick()
            runtime.read_login(io.StringIO(json.dumps(status)), value.diagnostics)
            value.tick()
        self.assertEqual(output.getvalue().count(url), 1)
        self.assertIn("BackendState=NeedsLogin; AuthURL=present", output.getvalue())
        self.assertIn("JSON received", output.getvalue())
        start.assert_not_called()
        popen.assert_not_called()
        self.assertNotIn("PRIVATE-SECRET", output.getvalue())

    def test_diagnostics_reject_unknown_status_and_urls_without_echoing_values(self):
        diagnostics = runtime.Diagnostics()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            diagnostics.status({"BackendState": "PRIVATE-SECRET", "AuthURL": "https://evil.example/PRIVATE-SECRET"})
            runtime.read_login(io.StringIO(json.dumps({"AuthURL": "https://login.tailscale.com/a/fake?secret=PRIVATE-SECRET"})), diagnostics)
            diagnostics.status({"BackendState": "NeedsLogin"})
        self.assertIn("rejected by validation", output.getvalue())
        self.assertIn("unavailable/unknown", output.getvalue())
        self.assertIn("AuthURL=absent", output.getvalue())
        self.assertNotIn("PRIVATE-SECRET", output.getvalue())
        self.assertNotIn("evil.example", output.getvalue())

    def test_error_streams_and_json_errors_only_emit_fixed_categories(self):
        diagnostics = runtime.Diagnostics()
        output = io.StringIO()
        secret = "PRIVATE-SECRET tskey-auth-SECRET user@example.com https://evil.example/state"
        with contextlib.redirect_stdout(output):
            runtime.read_errors(io.StringIO("TLS error " + secret), diagnostics, "daemon")
            runtime.read_errors(io.StringIO("permission denied " + secret), diagnostics, "login stderr")
            runtime.read_login(io.StringIO(json.dumps({"Error": "lookup failed " + secret, "State": secret})), diagnostics)
            diagnostics.error(secret, "CLI query", required=True)
        for category in ("TLS connection", "permission denied", "DNS resolution", "unclassified failure/output"):
            self.assertIn(category, output.getvalue())
        for token in secret.split():
            self.assertNotIn(token, output.getvalue())

    def test_reader_exceptions_eof_and_oversized_invalid_output_are_safe(self):
        output = io.StringIO()
        broken = Mock()
        broken.readline.side_effect = RuntimeError("PRIVATE-SECRET")
        with contextlib.redirect_stdout(output):
            runtime.read_login(broken)
            runtime.read_errors(broken, runtime.Diagnostics(), "daemon")
            runtime.read_login(io.StringIO("PRIVATE-SECRET" * 10000))
        self.assertIn("exception (details withheld)", output.getvalue())
        self.assertIn("buffer limit reached", output.getvalue())
        self.assertIn("EOF", output.getvalue())
        self.assertNotIn("PRIVATE-SECRET", output.getvalue())

    def test_diagnostics_budget_is_bounded(self):
        diagnostics = runtime.Diagnostics()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            for number in range(1000):
                diagnostics.auth_url("https://login.tailscale.com/a/fake" + str(number), "status fallback")
                diagnostics.error("TLS error PRIVATE-SECRET", "daemon")
        self.assertEqual(len(diagnostics.urls), 32)
        self.assertLessEqual(len(output.getvalue().splitlines()), 33)
        self.assertNotIn("PRIVATE-SECRET", output.getvalue())

    def test_login_pid_exit_and_pipe_routing_are_visible_without_raw_errors(self):
        value = self.supervisor()
        process = Mock(pid=123, returncode=7)
        process.poll.return_value = 7
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             patch.object(runtime.subprocess, "Popen", return_value=process) as popen, \
             patch.object(runtime.threading, "Thread"), \
             patch.object(runtime, "command_json", return_value={"BackendState": "NeedsLogin"}):
            value.start_login()
            value.tick()
        self.assertIn("start_login()", output.getvalue())
        self.assertIn("PID=123", output.getvalue())
        self.assertIn("exitcode=7", output.getvalue())
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.PIPE)
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertNotIn("--auth-key", popen.call_args.args[0])

    def test_query_failure_sanitizes_stderr_and_exception_text(self):
        result = Mock(returncode=1, stderr="x509 PRIVATE-SECRET", stdout="PRIVATE-SECRET")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(runtime.subprocess, "run", return_value=result):
            self.assertIsNone(runtime.command_json(runtime.cli_args("status", "--json"), runtime.Diagnostics()))
        self.assertIn("certificate validation", output.getvalue())
        self.assertNotIn("PRIVATE-SECRET", output.getvalue())

    def test_correct_funnel_never_reconfigured(self):
        value = self.supervisor()
        with patch.object(runtime, "command_json", side_effect=[STATUS, CONFIG] * 2), \
             patch.object(runtime, "gateway_ready", return_value=True), \
             patch.object(runtime.subprocess, "Popen") as popen:
            value.tick()
            value.tick()
            popen.assert_not_called()

    def test_wrong_target_reconfigured_without_state_reset(self):
        wrong = copy.deepcopy(CONFIG)
        wrong["Web"][DOMAIN + ":443"]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:8123"
        value = self.supervisor()
        with patch.object(runtime, "command_json", side_effect=[STATUS, wrong]), \
             patch.object(runtime, "gateway_ready", return_value=True), \
             patch.object(runtime.subprocess, "Popen") as popen:
            value.tick()
            self.assertIn(runtime.ORIGIN, popen.call_args.args[0])
            self.assertNotIn("reset", popen.call_args.args[0])
            self.assertNotIn("off", popen.call_args.args[0])

    def test_config_failure_does_not_mutate_or_log_json(self):
        value = self.supervisor()
        with patch.object(runtime, "command_json", side_effect=[STATUS, None]), \
             patch.object(runtime, "gateway_ready", return_value=True), \
             patch.object(runtime.subprocess, "Popen") as popen:
            value.tick()
            popen.assert_not_called()
        with patch.object(runtime.subprocess, "run", side_effect=subprocess.TimeoutExpired("fake-state-secret", 3)):
            self.assertIsNone(runtime.command_json(["tailscale"]))

    def test_no_funnel_null_is_empty_but_invalid_status_is_not(self):
        result = Mock(returncode=0, stdout="null")
        with patch.object(runtime.subprocess, "run", return_value=result):
            self.assertEqual(runtime.command_json(runtime.cli_args("funnel", "status", "--json")), {})
            self.assertIsNone(runtime.command_json(runtime.cli_args("status", "--json")))

    def test_failed_settings_are_retried_without_login(self):
        value = self.supervisor()
        value.settings_ready = False
        with patch.object(runtime, "command_json", return_value=STATUS), \
             patch.object(runtime, "gateway_ready") as gateway, \
             patch.object(runtime.subprocess, "run", return_value=Mock(returncode=1, stderr="DNS failed")) as run, \
             patch.object(value, "start_login") as login:
            value.tick()
            value.tick()
            run.assert_called_once()
            value.next_settings = 0
            value.tick()
            self.assertEqual(run.call_count, 2)
            self.assertIn("set", run.call_args.args[0])
            login.assert_not_called()
            gateway.assert_not_called()

    def test_transient_states_recover_without_login(self):
        value = self.supervisor()
        value.settings_ready = False
        states = [None, {}, {"BackendState": "NoState"}, {"BackendState": "Starting"},
                  {"BackendState": "Stopped"}, {"BackendState": "NeedsMachineAuth"},
                  {**STATUS, "Self": {"Online": False}}, STATUS, CONFIG]
        with patch.object(runtime, "command_json", side_effect=states), \
             patch.object(runtime, "gateway_ready", return_value=True), \
             patch.object(runtime.subprocess, "run", return_value=Mock(returncode=0)) as run, \
             patch.object(runtime.subprocess, "Popen") as popen:
            for _ in range(7):
                value.tick()
                self.assertLessEqual(value.retry_delay, 30)
                run.assert_not_called()
            value.tick()
            self.assertTrue(value.settings_ready)
            run.assert_called_once_with(runtime.settings_args(value.socket), capture_output=True,
                                        text=True, timeout=3, check=False, stdin=subprocess.DEVNULL)
            popen.assert_not_called()
            self.assertEqual(value.retry_delay, 1)

    def test_transient_query_failures_back_off_without_login(self):
        value = self.supervisor()
        value.settings_ready = False
        failures = [OSError("socket not ready"),
                    Mock(returncode=1, stderr="lookup DNS failed"),
                    subprocess.TimeoutExpired("status", 3),
                    Mock(returncode=0, stdout="invalid JSON")]
        with patch.object(runtime.subprocess, "run", side_effect=failures), \
             patch.object(runtime.subprocess, "Popen") as popen:
            delays = []
            for _ in failures:
                value.tick()
                delays.append(value.retry_delay)
            self.assertEqual(delays, [2, 4, 8, 16])
            popen.assert_not_called()
        with patch.object(runtime, "command_json", return_value=None):
            for _ in range(10):
                value.tick()
            self.assertEqual(value.retry_delay, 30)

    def test_startup_race_nostate_running_ready_discards_error_categories(self):
        output = io.StringIO()
        with patch.object(runtime.time, "monotonic", return_value=100) as clock, \
             contextlib.redirect_stdout(output):
            value = self.supervisor()
            with patch.object(runtime, "command_json", side_effect=[
                    {"BackendState": "NoState"}, STATUS, CONFIG]), \
                 patch.object(runtime, "gateway_ready", return_value=True), \
                 patch.object(runtime.subprocess, "Popen") as popen, \
                 patch.object(runtime.subprocess, "run") as run:
                runtime.read_errors(io.StringIO(
                    "no such file PRIVATE-SECRET\npermission denied PRIVATE-SECRET\n"
                    "lookup PRIVATE-SECRET\nfailed PRIVATE-SECRET\n"), value.diagnostics, "daemon")
                value.tick()
                clock.return_value = 105
                value.tick()
                clock.return_value = 131
                value.diagnostics.finish_startup()
                popen.assert_not_called()
                run.assert_not_called()
            self.assertEqual(value.diagnostics.pending_errors, {})
            self.assertTrue(value.settings_ready)
        text = output.getvalue()
        for message in ("BackendState=NoState", "not ready yet", "BackendState=Running", "Ready:"):
            self.assertIn(message, text)
        for message in ("file/socket unavailable", "permission denied", "DNS resolution",
                        "unclassified failure/output", "timeout", "PRIVATE-SECRET"):
            self.assertNotIn(message, text)
        for line in text.splitlines():
            datetime.fromisoformat(line.split(" ", 1)[0])

    def test_startup_timeout_emits_safe_categories_only_after_grace(self):
        output = io.StringIO()
        with patch.object(runtime.time, "monotonic", return_value=100) as clock, \
             contextlib.redirect_stdout(output):
            value = self.supervisor()
            runtime.read_errors(io.StringIO("TLS error PRIVATE-SECRET\n"),
                                value.diagnostics, "daemon")
            result = Mock(returncode=1, stderr="permission denied PRIVATE-SECRET")
            with patch.object(runtime.subprocess, "run", return_value=result), \
                 patch.object(runtime.subprocess, "Popen") as popen:
                value.tick()
                clock.return_value = 129.99
                value.tick()
                self.assertNotIn("permission denied", output.getvalue())
                self.assertNotIn("timeout", output.getvalue())
                self.assertNotIn("TLS connection", output.getvalue())
                clock.return_value = 130
                value.tick()
                value.tick()
                popen.assert_not_called()
            self.assertTrue(value.settings_ready)
        text = output.getvalue()
        self.assertIn("readiness timeout (30 seconds); CLI query: permission denied", text)
        self.assertEqual(text.count("CLI query: permission denied"), 1)
        self.assertIn("readiness timeout (30 seconds); daemon: TLS connection", text)
        self.assertEqual(text.count("daemon: TLS connection"), 1)
        self.assertIn("backend not ready; existing state retained", text)
        self.assertNotIn("PRIVATE-SECRET", text)
        for line in text.splitlines():
            datetime.fromisoformat(line.split(" ", 1)[0])

    def test_needs_login_starts_once_even_after_failure(self):
        value = self.supervisor()
        value.settings_ready = False
        process = Mock(pid=123, returncode=1)
        process.poll.return_value = 1
        with patch.object(runtime, "command_json", return_value={"BackendState": "NeedsLogin"}), \
             patch.object(runtime.threading, "Thread"), \
             patch.object(runtime.subprocess, "Popen", return_value=process) as popen:
            for _ in range(10):
                value.tick()
            popen.assert_called_once()
            self.assertIn("up", popen.call_args.args[0])
            self.assertTrue(value.login_attempted)

    def test_login_completion_applies_settings_once_then_preserves_funnel(self):
        value = self.supervisor()
        value.settings_ready = False
        process = Mock(pid=123, returncode=0)
        process.poll.side_effect = [None, 0]
        with patch.object(runtime, "command_json", side_effect=[
                {"BackendState": "NeedsLogin"}, {"BackendState": "NeedsLogin"},
                STATUS, CONFIG, STATUS, CONFIG]), \
             patch.object(runtime.threading, "Thread"), \
             patch.object(runtime.subprocess, "Popen", return_value=process) as popen, \
             patch.object(runtime.subprocess, "run", return_value=Mock(returncode=0)) as run, \
             patch.object(runtime, "gateway_ready", return_value=True):
            for _ in range(4):
                value.tick()
            popen.assert_called_once()
            run.assert_called_once()
            self.assertTrue(value.settings_ready)
            self.assertIsNone(value.login)

    def test_run_reuses_authenticated_state_across_supervisor_restarts(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            state = data / "tailscaled.state"
            state.write_bytes(b"fixture-private-state")
            for _ in range(3):
                value = runtime.Supervisor(data)
                daemon = Mock()
                daemon.poll.return_value = None
                def stop_after_tick(_delay):
                    value.stop.set()
                with patch.object(runtime, "command_json", side_effect=[STATUS, CONFIG]), \
                     patch.object(runtime, "gateway_ready", return_value=True), \
                     patch.object(runtime.subprocess, "run", return_value=Mock(returncode=0)) as run, \
                     patch.object(runtime.subprocess, "Popen", return_value=daemon) as popen, \
                     patch.object(runtime.threading, "Thread"), \
                     patch.object(value.stop, "wait", side_effect=stop_after_tick):
                    self.assertEqual(value.run(), 0)
                    popen.assert_called_once_with(runtime.daemon_args(data), stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                 text=True, encoding="utf-8", errors="replace")
                    self.assertIn("set", run.call_args.args[0])
                    self.assertEqual(state.read_bytes(), b"fixture-private-state")

    @unittest.skip("Upstream workflow is not shipped in this source-built catalog")
    def test_workflow_manual_and_branch_push_builds_without_publication(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/funnel-test.yml").read_text())
        self.assertEqual(workflow.get("on", workflow.get(True)), {
            "workflow_dispatch": None,
            "push": {"branches": ["main"]},
        })
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        job = workflow["jobs"]["containers"]
        self.assertEqual({row["platform"] for row in job["strategy"]["matrix"]["include"]},
                         {"linux/amd64", "linux/arm64"})
        build = next(row for row in job["steps"] if row.get("uses", "").startswith("docker/build-push"))
        self.assertIs(build["with"]["push"], False)
        self.assertNotIn("secrets.", json.dumps(workflow))

    def test_container_fixture_binaries_compile_without_credentials(self):
        spec = importlib.util.spec_from_file_location("funnel_container_test", ADDON / "container_test.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        compile(fixture.FAKE_CLI, "fake_cli", "exec")
        compile(fixture.FAKE_DAEMON, "fake_daemon", "exec")
        compile(fixture.BOOTSTRAP_PROBE, "bootstrap_probe", "exec")
        compile(fixture.PREPARE_PROBE, "prepare_probe", "exec")

    def test_schema_validation_checks_tls_hostname_target_and_persistence(self):
        self.assertTrue(runtime.funnel_correct(CONFIG, DOMAIN))
        for key in CONFIG:
            wrong = copy.deepcopy(CONFIG)
            wrong.pop(key)
            self.assertFalse(runtime.funnel_correct(wrong, DOMAIN))
        self.assertFalse(runtime.funnel_correct(CONFIG, "other.test-tail.ts.net"))
        self.assertFalse(runtime.funnel_correct({"Foreground": CONFIG}, DOMAIN))
        self.assertFalse(runtime.funnel_correct({**CONFIG, "unexpected": True}, DOMAIN))
        self.assertFalse(runtime.connected({**STATUS, "Self": {"Online": False}}))

    def test_http_health_requires_200_no_redirect_or_env_proxy(self):
        response = Mock()
        response.status = 200
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(runtime.urllib.request, "build_opener", return_value=opener):
            self.assertTrue(runtime.gateway_ready())
            opener.open.assert_called_with(runtime.ORIGIN + "/health", timeout=2)
            response.status = 302
            self.assertFalse(runtime.gateway_ready())
            opener.open.side_effect = OSError("fake-secret")
            self.assertFalse(runtime.gateway_ready())

    def test_terminate_waits_and_escalates_only_if_stuck(self):
        process = Mock()
        process.poll.return_value = None
        runtime.stop_process(process)
        process.terminate.assert_called_once()
        process.kill.assert_not_called()
        process.wait.side_effect = [subprocess.TimeoutExpired("tailscaled", 8), 0]
        runtime.stop_process(process)
        process.kill.assert_called_once()

    def test_stop_event_shuts_daemon_without_off_logout_or_reset(self):
        value = self.supervisor()
        value.stop.set()
        with patch.object(runtime.subprocess, "Popen", return_value=value.daemon) as popen:
            self.assertEqual(value.run(), 0)
            value.daemon.terminate.assert_called_once()
            self.assertEqual(popen.call_args.args[0], runtime.daemon_args())

    def test_main_signal_handlers_request_orderly_shutdown(self):
        value = self.supervisor()
        handlers = {}
        def install(signum, handler):
            handlers[signum] = handler
        def run():
            handlers[runtime.signal.SIGTERM](runtime.signal.SIGTERM, None)
            self.assertTrue(value.stop.is_set())
            value.stop.clear()
            handlers[runtime.signal.SIGINT](runtime.signal.SIGINT, None)
            self.assertTrue(value.stop.is_set())
            return 0
        with patch.object(runtime, "Supervisor", return_value=value), \
             patch.object(runtime, "prepare"), \
             patch.object(runtime.signal, "signal", side_effect=install), \
             patch.object(value, "run", side_effect=run):
            self.assertEqual(runtime.main(), 0)

    def test_health_is_local_readonly(self):
        text = (ADDON / "healthcheck.py").read_text()
        self.assertNotIn("https://", text)
        spec = importlib.util.spec_from_file_location("funnel_health", ADDON / "healthcheck.py")
        health = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"runtime": runtime}):
            spec.loader.exec_module(health)
        with patch.object(health, "command_json", side_effect=[STATUS, CONFIG]), \
             patch.object(health, "gateway_ready", return_value=True):
            self.assertTrue(health.check())
        with patch.object(health, "command_json", return_value=None):
            self.assertFalse(health.check())


if __name__ == "__main__":
    unittest.main()
