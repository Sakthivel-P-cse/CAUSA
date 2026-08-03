# Causa Dev-A ingestion image — runs the RCA-trigger service.
# Build context is the repository root:
#   docker build -f incidents/Dockerfile -t causa/ingest:latest .
FROM python:3.12-slim

WORKDIR /app

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
COPY testbed/ ./testbed/

RUN pip install --no-cache-dir ".[trigger]"

RUN useradd --uid 10001 --create-home causa
USER causa

EXPOSE 8080

CMD ["python", "-m", "ingest.trigger.app"]
