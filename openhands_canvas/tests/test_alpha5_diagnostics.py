"""Alpha.5 diagnostics: safe metadata, sampler lifecycle, and integration."""
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openhands_canvas import ha_canvas_diagnostic as diag
from openhands_canvas.upstream_logs import normalize


def frame(module, function, line, parent=None):
    return SimpleNamespace(f_globals={"__name__":module},
        f_code=SimpleNamespace(co_name=function),f_lineno=line,f_back=parent)


class Diagnostics(unittest.TestCase):
    def test_prefers_openhands_caller_over_library(self):
        caller=frame("openhands.agent_server.settings_router","get_settings",153)
        inner=frame("tenacity.nap","sleep",42,caller)
        self.assertEqual(diag._frame_signature(inner),
                         ("openhands.agent_server.settings_router","get_settings",153))
        self.assertEqual(diag._frame_signature(frame("libtmux.helpers","retry",33)),
                         ("libtmux.helpers","retry",33))
        self.assertEqual(diag._frame_signature(frame("__main__","main",210)),
                         ("__main__","main",210))
        self.assertIsNone(diag._frame_signature(frame("asyncio.base_events","run_forever",180,
                frame("openhands.agent_server.settings_router","get_settings",153))))
        self.assertEqual(diag._frame_signature(
            frame("selectors", "select", 44,
                  frame("libtmux.common", "communicate", 78,
                        frame("openhands.agent_server.api", "cleanup", 256)))),
            ("openhands.agent_server.api", "cleanup", 256))
        self.assertIsNone(diag._frame_signature(
            frame("selectors", "select", 44,
                  frame("asyncio.base_events", "_run_once", 1690))))
        self.assertEqual(diag._frame_signature(
            frame("threading", "wait", 20,
                  frame("openhands.agent_server.api", "startup", 245))),
            ("openhands.agent_server.api", "startup", 245))
        self.assertIsNone(diag._frame_signature(frame("private.user","token_value",10)))

    def test_rolling_window_accepts_line_jitter_and_rejects_idle(self):
        items=[("openhands.sdk.x","retry",n) for n in [11,12,13,11,12,11]*2]
        self.assertEqual(diag._dominant_signature(items),
                         (("openhands.sdk.x","retry",11),12))
        self.assertIsNone(diag._dominant_signature(items[:11]))
        self.assertIsNone(diag._dominant_signature([None]*20))

    def test_started_marker_and_safe_payload(self):
        capture=io.StringIO()
        with patch.object(diag.sys,"stderr",capture):
            diag._marker("sampler_started")
            diag._emit(("openhands.agent_server.settings_router","get_settings",153),900)
        lines=[json.loads(x) for x in capture.getvalue().splitlines()]
        self.assertEqual(lines[0],{"logger":"openhands","level":"WARNING","diagnostic_event":"sampler_started"})
        self.assertEqual(lines[1]["diagnostic_samples"],600)
        self.assertEqual(set(lines[1]),{"logger","level","diagnostic_event","diagnostic_module",
                                       "diagnostic_function","diagnostic_lineno","diagnostic_samples"})
        self.assertNotIn("api_key",capture.getvalue())

    def test_failed_thread_start_never_fails_import(self):
        capture=io.StringIO()
        with patch.object(diag.threading.Thread,"start",side_effect=RuntimeError("private")), \
             patch.object(diag.sys,"stderr",capture):
            self.assertIsNone(diag.start())
        record=json.loads(capture.getvalue())
        self.assertEqual(record["diagnostic_event"],"sampler_unavailable")
        self.assertNotIn("private",capture.getvalue())

    def test_normalizer_strictly_validates_metadata(self):
        events=[]
        logger=SimpleNamespace(log=lambda severity,msg:events.append((severity,msg)))
        rec={"logger":"openhands","level":"WARNING",
             "diagnostic_event":"main_thread_stalled",
             "diagnostic_module":"__main__",
             "diagnostic_function":"main",
             "diagnostic_lineno":153,"diagnostic_samples":12}
        normalize(json.dumps(rec).encode(),logger,"stderr")
        self.assertIn("agent_main_thread_stalled",events[-1][1])
        self.assertIn("module=__main__",events[-1][1])
        rec["diagnostic_module"]="TOKEN_NEVER_PRINT\nprivate=x"
        normalize(json.dumps(rec).encode(),logger,"stderr")
        self.assertNotIn("TOKEN_NEVER_PRINT",events[-1][1])
        self.assertEqual(events[-1][1],"structured_output component=openhands stream=stderr")
        normalize(b'{"logger":"openhands","level":"WARNING","diagnostic_event":"sampler_started"}',logger,"stderr")
        self.assertEqual(events[-1][1],"agent_sampler_started component=openhands stream=stderr")

    def test_runtime_hook_isolated_and_build_checks(self):
        root=Path(__file__).resolve().parents[1]
        runtime=(root/"native_runtime.py").read_text()
        docker=(root/"Dockerfile").read_text()
        self.assertIn("'--import-modules', 'canvas_ui_tool,ha_canvas_diagnostic'",runtime)
        self.assertIn("OH_EXTRA_PYTHON_PATH='/opt/ha-canvas-diag:/opt/agent-canvas/tools'",runtime)
        self.assertIn("COPY ha_canvas_diagnostic.py /opt/ha-canvas-diag/",docker)
        self.assertIn('"py_compile", "/opt/ha-canvas-diag/ha_canvas_diagnostic.py"',docker)
        self.assertNotIn("CAP_SYS_PTRACE",runtime+docker)
        self.assertNotIn("faulthandler",runtime+docker)


if __name__=="__main__":unittest.main()
