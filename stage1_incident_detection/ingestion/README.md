# Stage-1 Ingestion Module

A **loosely-coupled, drop-in ingestion module**. It reads from the Stage-1 OTel
stack (Tempo / Prometheus / Loki) and normalizes **one incident window** into a
single typed object — `IncidentTelemetry` — that any downstream module (RCA
engine, dashboard, evaluation harness) consumes.

## The contract (why this is a *module*, not just a stack)

The dependency arrow points **upstream, into ingestion**:

```
        ┌────────────────────────────────────────────┐
        │  ingestion  (this module)                   │
        │    owns:  TraceSpan, ServiceEdge, InfraNode, │
        │           *Envelope, IncidentTelemetry       │
        └───────────────▲──────────────────────────────┘
                        │ import (types + reader)
     ┌──────────────────┴───────────────────┐
   RCA engine        dashboard          eval harness
```

Consumers import the **normalized types** from here and never touch raw
Tempo/Prom/Loki responses. Ingestion imports **nothing** from RCA, remediation,
or the dashboard — so it drops into any project that speaks this contract, and
the storage backends behind it are swappable without changing a single consumer.

## Public API

```python
from ingestion import TelemetryReader
from datetime import datetime, timezone

reader = TelemetryReader.from_defaults()          # wired to in-cluster backends
incident = reader.read_incident_window(
    incident_id="inc-123",
    alerted_service="payment-service",
    around=datetime.now(timezone.utc),
    lookback_minutes=10,
    namespace="apps",                             # enables container CPU/mem series
)
```

`read_incident_window(...)` returns one `IncidentTelemetry`:

| Field | Type | Source |
|---|---|---|
| `spans` | `list[SpanEnvelope]` | Tempo trace, DAG-enriched (`dag_depth`, `logical_seq`) |
| `service_edges` | `list[ServiceEdge]` | Prometheus `traces_service_graph_*` |
| `infra_nodes` | `list[InfraNode]` | Prometheus (kube-state-metrics, node-exporter) |
| `metric_points` | `list[MetricPointEnvelope]` | Prometheus range queries (error/latency/rate, container CPU/mem) |
| `log_records` | `list[LogRecordEnvelope]` | Loki (error logs) |
| `references` | `list[TelemetryReference]` | back-pointers to raw payloads (never copied) |

Signals are **kept distinct** (spans ≠ metric points ≠ log records), per PRD2 §4.
Raw telemetry is **referenced, not copied** — `TelemetryReference` records the
backend + locator + window so a consumer can re-fetch on demand.

Helpers on the object:

```python
incident.spans_as_trace_spans()                 # unwrap to bare TraceSpan[]
incident.metric_series("payment-service", "error_rate")   # [[ts, value], ...]
incident.to_summary()                           # compact counts, safe to log/persist
```

## Configuration

Endpoints default to the in-cluster services; override via `TelemetryReader(...)`
or `from_defaults(prometheus_url=..., tempo_url=..., loki_url=...)`.

Scope / naming env knobs (see `service_scope.py`):

| Env var | Purpose |
|---|---|
| `TRACEOPS_MONITORED_SERVICES_REGEX` | allowlist; if set, only matches are in scope |
| `TRACEOPS_EXCLUDED_SERVICES_REGEX` | extra excludes, ORed with the internal-stack defaults |
| `TRACEOPS_SERVICE_NAME_LABEL_KEYS` | ordered label keys used to read a service name |

## Layout

```
ingestion/
├── __init__.py          # public API surface (import from here)
├── contract.py          # the typed contract — this module owns these types
├── reader.py            # TelemetryReader.read_incident_window() — the normalization boundary
├── service_scope.py     # which services are "app" services; pod→service naming
├── clients/
│   ├── tempo_client.py
│   ├── prometheus_client.py
│   └── loki_client.py
└── requirements.txt      # httpx
```

## Install

```bash
pip install -r ingestion/requirements.txt
```
