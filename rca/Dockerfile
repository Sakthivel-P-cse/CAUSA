# Causa Stage-2 RCA image.
# Build context is the repository root:
#   docker build -f rca/Dockerfile -t causa/stage2-rca:latest .
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
  && curl -fsSL -o /usr/local/bin/kubectl https://dl.k8s.io/release/v1.31.0/bin/linux/amd64/kubectl \
  && chmod +x /usr/local/bin/kubectl \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY shared/ ./shared/
COPY incidents/ingest/ ./incidents/ingest/
COPY rca/anomaly/ ./rca/anomaly/
COPY rca/graphs/ ./rca/graphs/
COPY rca/isolation/ ./rca/isolation/
COPY rca/engine/ ./rca/engine/
COPY rca/scoring/ ./rca/scoring/
COPY rca/adapters/ ./rca/adapters/
COPY rca/classify/ ./rca/classify/
COPY eval/ ./eval/
COPY remediation/ ./remediation/
COPY dashboard/backend/ /app/dashboard/backend/
COPY dashboard/__init__.py /app/dashboard/__init__.py

RUN pip install --no-cache-dir ".[trigger]"

RUN useradd --uid 10001 --create-home causa
USER causa
ENV PYTHONPATH=/app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"]

CMD ["python", "-m", "rca.trigger.app"]
