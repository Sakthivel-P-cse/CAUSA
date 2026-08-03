FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
  && curl -fsSL -o /usr/local/bin/kubectl https://dl.k8s.io/release/v1.31.0/bin/linux/amd64/kubectl \
  && chmod +x /usr/local/bin/kubectl \
  && rm -rf /var/lib/apt/lists/*
COPY requirements-devc.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt httpx "psycopg[binary]"
COPY .import /app/.import
COPY shared /app/shared
COPY incidents /app/incidents
COPY rca /app/rca
COPY remediation /app/remediation
COPY eval /app/eval
COPY testbed /app/testbed
COPY dashboard /app/dashboard
ENV PYTHONPATH=/app/.import:/app
USER 1000
CMD ["python", "-m", "remediation.worker"]
