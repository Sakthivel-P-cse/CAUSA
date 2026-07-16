# TraceOps · Telemetry Web Dashboards

Three standalone web dashboards that display **live telemetry data** directly in the browser — no Grafana required.

| Port | Dashboard | Data Source | Script |
|-----:|-----------|-------------|--------|
| 5001 | 📋 Logs     | Loki `:3100`       | `logs_dashboard.py` |
| 5002 | 📊 Metrics  | Prometheus `:9090` | `metrics_dashboard.py` |
| 5003 | 🔍 Traces   | Tempo `:3200`      | `traces_dashboard.py` |

All pages **auto-refresh every 10 seconds** using vanilla JavaScript — no external dependencies needed.

---

## Quick Start

### Step 1 — Port-forward the backends

Open a terminal and run (each line is a background process):

```bash
kubectl -n loki       port-forward svc/loki       3100:3100 &
kubectl -n prometheus port-forward svc/prometheus 9090:9090 &
kubectl -n tempo      port-forward svc/tempo      3200:3200 &
```

### Step 2 — Start all dashboards

```bash
cd /home/sakthi/projects/traceops/stage1_incident_detection/dashboards
bash start_dashboards.sh
```

### Step 3 — Open in browser

| Dashboard | URL |
|-----------|-----|
| Logs      | http://localhost:5001 |
| Metrics   | http://localhost:5002 |
| Traces    | http://localhost:5003 |

---

## Run dashboards individually

```bash
# Logs dashboard only
python3 logs_dashboard.py

# Metrics dashboard only
python3 metrics_dashboard.py

# Traces dashboard only
python3 traces_dashboard.py
```

---

## What each dashboard shows

### Logs (`:5001`)
- Live log stream from all Loki streams (last 15 minutes, 100 lines)
- Color-coded by level: 🔴 ERROR · 🟡 WARN · 🟢 INFO · 🟣 DEBUG
- Stats bar: total lines, error count, warn count, info count

### Metrics (`:5002`)
- 12 key OTel Collector metrics as cards (spans/s, metrics pts/s, logs/s, CPU, memory, queue size, dropped spans, etc.)
- Full raw results table with label filters
- Queries Prometheus `/api/v1/query`

### Traces (`:5003`)
- Table of recent traces from Tempo (last 30)
- Columns: TraceID (clickable → raw JSON), Root Service, Root Span, Duration, Start Time
- Stats bar: total traces, unique services, avg duration, slow traces (>1s)
- Duration color-coded: 🟢 <200ms · 🟡 <1s · 🔴 ≥1s

---

## No data showing?

1. **Loki/Prometheus/Tempo not reachable** → check port-forwards are running
2. **No traces** → send traffic to instrumented microservices first
3. **Logs empty** → check OTel Collector is running and forwarding to Loki
