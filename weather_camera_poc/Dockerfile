FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/data/model-cache HF_HUB_DISABLE_TELEMETRY=1 \
    CUDA_VISIBLE_DEVICES="" OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
WORKDIR /app
COPY requirements.txt ./
# CPU wheel index supports both Linux x86_64 and aarch64; no model download here.
RUN pip install --no-cache-dir torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt
COPY app.py models.py worker.py run.sh ./
RUN chmod 755 /app/run.sh
ENTRYPOINT ["/app/run.sh"]
