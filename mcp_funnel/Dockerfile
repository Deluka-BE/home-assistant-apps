FROM tailscale/tailscale:v1.102.4@sha256:2667499ed87ae29218f292556ba062918402dd5e92e93637af14867e4df12dd3 AS tailscale
FROM python:3.13.12-slim-bookworm@sha256:a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6
LABEL io.hass.version="0.1.0" io.hass.type="app" io.hass.arch="amd64|aarch64" \
      org.opencontainers.image.source="https://github.com/Deluka-BE/hevy-personal-mcp"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscaled /usr/local/bin/
COPY runtime.py healthcheck.py /opt/mcp-funnel/
WORKDIR /opt/mcp-funnel
HEALTHCHECK --interval=30s --timeout=15s --start-period=60s --retries=3 CMD ["python", "/opt/mcp-funnel/healthcheck.py"]
ENTRYPOINT ["python", "/opt/mcp-funnel/runtime.py"]
