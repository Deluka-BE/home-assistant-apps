FROM ghcr.io/home-assistant/base-python:3.13-alpine3.23

LABEL io.hass.version="0.1.0" io.hass.type="app" io.hass.arch="aarch64"

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
COPY src/apple_music_mcp/ ./src/apple_music_mcp/
RUN python3 -m pip install --no-cache-dir .

# Run Python as PID 1, handling SIGTERM/SIGINT directly instead of starting s6.
ENTRYPOINT ["python3", "-m", "apple_music_mcp.haos_fake"]
