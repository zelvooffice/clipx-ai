# CLIPX AI processing image: API, Celery workers, live monitor (FFmpeg + OpenCV + faster-whisper).
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models WORK_DIR=/work LOCAL_STORAGE_DIR=/data/storage

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-inter fontconfig curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f

WORKDIR /app
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install . && rm -rf app
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN useradd --system --uid 10001 --home /app clipx \
 && mkdir -p /models /work /data/storage && chown -R clipx /models /work /data
USER clipx

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "2"]
