FROM debian:trixie-slim
ARG TARGETARCH
ARG UPSTREAM_REV=6226528a37e8fa7e0ec72e3e96dd280bef20d532
ENV DEBIAN_FRONTEND=noninteractive DISPLAY=:0 APPLEMUSIC_CONFIG_DIR=/data/config APPLEMUSIC_DATA_DIR=/data/browser APPLEMUSIC_CACHE_DIR=/data/cache APPLEMUSIC_BROWSER_NO_SANDBOX=1 PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 PULSE_SERVER=unix:/run/audio/pulse.sock
RUN test "$TARGETARCH" = arm64 \
 && apt-get update && apt-get install -y --no-install-recommends ca-certificates openssl wget git python3 python3-venv xvfb x11vnc novnc websockify nginx openbox pulseaudio-utils libasound2-plugins tini \
 && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_arm64.deb -O /tmp/chrome.deb \
 && apt-get install -y /tmp/chrome.deb && rm /tmp/chrome.deb \
 && git clone https://github.com/epheterson/applemusic-mcp.git /opt/upstream \
 && git -C /opt/upstream checkout "$UPSTREAM_REV" \
 && python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir /opt/upstream 'mcp==1.30.0' \
 && rm -rf /var/lib/apt/lists/* /opt/upstream/.git
COPY server.py isrc_lookup.py spotify_source.py playlist_ops.py discovery.py web_ops.py local_tools.py security.py run.py nginx.conf /opt/addon/
COPY release.json config.yaml Dockerfile /opt/addon/
LABEL io.hass.name="Apple Music Browser" io.hass.description="Apple Music browser control using upstream applemusic-mcp" io.hass.arch="aarch64" io.hass.type="addon" io.hass.version="0.1.10"
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/venv/bin/python", "/opt/addon/run.py"]
