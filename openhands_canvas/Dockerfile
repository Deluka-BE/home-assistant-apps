FROM ghcr.io/openhands/agent-canvas@sha256:78c4d12c6f537343fc38e5d7af763962de3b89c012128312c8b3d9363d7f773f AS upstream
FROM scratch
COPY --from=upstream / /
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    ACP_NODE_DIR=/acp-node LC_ALL=C.UTF-8 LANG=C.UTF-8 LOG_JSON=true \
    OH_ACP_SKILL_SOURCING=openhands_managed EDITOR=code VISUAL=code GIT_EDITOR="code --wait" \
    OH_ENABLE_VSCODE=false OPENVSCODE_SERVER_ROOT=/openhands/.openvscode-server \
    CHROME_BIN=/usr/bin/chromium PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    CHROMIUM_FLAGS="--no-sandbox --disable-dev-shm-usage --disable-gpu" \
    LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu:/usr/lib:/usr/lib/x86_64-linux-gnu \
    AGENT_CANVAS_BASE_PATH=/canvas \
    OPENHANDS_BUILD_GIT_SHA=ccae18cb897bef7960f210aa3f380b577a84be7c \
    OPENHANDS_BUILD_GIT_REF=refs/tags/v1.59.0 \
    AGENT_CANVAS_BUILD_GIT_SHA=934612b3b6cee05d3a4400b98b48a9c69215401d \
    AGENT_CANVAS_BUILD_GIT_REF=refs/tags/v1.35.0
LABEL io.hass.name="OpenHands Canvas" \
      io.hass.description="Security-blocked OpenHands Canvas Alpha Supervisor wrapper" \
      io.hass.type="app" io.hass.arch="aarch64" io.hass.version="0.1.0-alpha.4" \
      org.opencontainers.image.title="OpenHands Canvas Supervisor wrapper" \
      org.opencontainers.image.source="https://github.com/Deluka-BE/home-assistant-apps"
COPY startup.py tls_proxy.py logging_utils.py upstream_logs.py /opt/ha-canvas/
COPY check_runtime.py /opt/ha-canvas/
COPY prepare_runtime.py frontend_runtime.py ingress_policy.py /opt/ha-canvas/
COPY native_ingress.py native_runtime.py /opt/ha-canvas/
USER root
RUN ["/usr/local/bin/python3", "-c", "import sys; from pathlib import Path; sys.path.insert(0, '/opt/ha-canvas'); from prepare_runtime import sanitize_static_server; p = Path('/opt/agent-canvas/static-server.mjs'); p.write_bytes(sanitize_static_server(p.read_bytes()))"]
RUN ["/usr/local/bin/python3", "/opt/ha-canvas/check_runtime.py"]
RUN ["/usr/local/bin/python3", "-c", "import sys; sys.path[:0] = ['/opt/ha-canvas', '/opt/agent-canvas/tools']; import native_runtime; from native_ingress import CanvasFiles; CanvasFiles('/opt/agent-canvas/frontend'); import openhands.agent_server, openhands.automation.app, canvas_ui_tool"]
WORKDIR /
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/python3", "/opt/ha-canvas/startup.py"]
