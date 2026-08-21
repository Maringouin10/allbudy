# Image legere, compatible amd64 et arm64 (Raspberry Pi 4/5, mini PC).
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    ALLBUDY_DATA_DIR=/data \
    ALLBUDY_HOST=0.0.0.0 \
    ALLBUDY_PORT=8088

WORKDIR /app

# curl sert au healthcheck; le reste des dependances est en roues precompilees.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY allbudy ./allbudy
COPY pyproject.toml README.md ./

# Compte non privilegie: le volume de donnees lui appartient.
RUN useradd --system --create-home --uid 10001 allbudy \
    && mkdir -p /data \
    && chown -R allbudy:allbudy /data /app
USER allbudy

VOLUME ["/data"]
EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8088/api/system/health || exit 1

CMD ["python", "-m", "allbudy.main"]
