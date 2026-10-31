FROM python:3.13-alpine
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/milo-dot
COPY milo_dot/ ./milo_dot/
RUN mkdir -p /data
ENTRYPOINT ["python", "-m", "milo_dot.container_start"]
