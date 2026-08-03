# `ingest/` — Stage-1 telemetry ingestion (Dev A, contract C1)

Reads the Stage-1 OTel stack (Tempo / Prometheus / Loki) and normalizes it into
the shared `contracts` types. The normalized types live in **`contracts/`** (the
cross-dev seam) — consumers import them from there, never from a client. Ingestion
imports nothing from RCA / remediation / dashboard, so it drops into any project
that speaks the contract and the storage backends behind it are swappable.

```
        contracts/        ← the seam: C1 telemetry, C2 anomaly, C5 ground truth
            ▲
            │ import (types)
   ┌────────┴─────────────────────────────┐
 ingest/ (this)     anomaly/ (C2)     RCA engine / eval (Dev B / C)
```

## Two surfaces

**1. The C1 adapters** — the fine-grained read API (Team_Split §3):

```python
from ingest import TelemetryAdapter
from contracts import Window
from datetime import datetime, timezone, timedelta

adapter = TelemetryAdapter.from_defaults(namespace="otel-demo")
now = datetime.now(timezone.utc)
win = Window(now - timedelta(minutes=10), now)

adapter.traces(win)                                 # -> list[SpanEnvelope]  (DAG-ordered)
adapter.metrics("cartservice", "error_rate", win)   # -> [[ts, value], ...]
adapter.logs("cartservice", win)                    # -> list[LogRecordEnvelope]
adapter.topology(win)                               # -> list[Edge]  (sync|async|runs-on)
```

**2. The one-shot bundle** — everything for one incident window in a single
`IncidentTelemetry`, built on top of the adapters:

```python
from ingest import TelemetryReader
from datetime import datetime, timezone

reader = TelemetryReader.from_defaults()
incident = reader.read_incident_window(
    incident_id="inc-123",
    alerted_service="cartservice",
    around=datetime.now(timezone.utc),
    lookback_minutes=10,
    namespace="otel-demo",
)
incident.spans          # SpanEnvelope[]  (span.kind + attributes populated)
incident.service_edges  # ServiceEdge[]   (call_type stamped from spans)
incident.edges          # Edge[]          (unified sync/async/runs-on topology)
incident.metric_series("cartservice", "error_rate")
```

Signals are kept **distinct** (spans ≠ metric points ≠ log records, PRD2 §4). Raw
telemetry is **referenced, not copied** (`TelemetryReference`).

### `call_type` (the Influence-Graph input, RCA_Engine_Spec §1.2)

Span parsing captures `kind` (CLIENT/SERVER/PRODUCER/CONSUMER) + attributes
(`messaging.system`, `rpc.system`, `db.system`, `http.*`). `ingest/topology.py`
turns those into edge `call_type`:

- `messaging.system` / PRODUCER-CONSUMER → **async** (kept in the Influence Graph)
- `rpc.system` / `db.system` / `http.*` / CLIENT-SERVER → **sync** (reversed)
- pod → host node (`kube_pod_info`) → **runs-on**

## The trigger service (`ingest/trigger/`)

An active `/trigger` + `/health` HTTP service that receives Alertmanager
webhooks, opens an incident window (via the reader), and records incident state
in Postgres. It runs **in front of** the Stage-2 engine (chain, don't replace).

```bash
python -m ingest.trigger.app          # needs the [trigger] extra (psycopg)
```

## Layout

```
ingest/
├── __init__.py          # TelemetryAdapter, TelemetryReader, clients
├── adapters.py          # C1 surface: traces / metrics / logs / topology
├── reader.py            # read_incident_window() bundle over the adapters
├── topology.py          # call_type derivation + unified Edge emission
├── ordering.py          # causality-safe span ordering (S3-robust)
├── service_scope.py     # app-vs-internal service scoping; pod→service naming
├── clients/             # tempo / prometheus / loki HTTP clients
├── trigger/             # active webhook service (app / webhook / state)
└── requirements.txt     # httpx  (psycopg via the `trigger` extra)
```

## Install

```bash
pip install -e .            # contracts + ingest + anomaly
pip install -e ".[trigger]" # + psycopg (to run the trigger service)
pip install -e ".[dev]"     # + pytest, respx (to run the tests)
```

## Env knobs (`service_scope.py`)

| Env var | Purpose |
|---|---|
| `CAUSA_MONITORED_SERVICES_REGEX` | allowlist; if set, only matches are in scope |
| `CAUSA_EXCLUDED_SERVICES_REGEX` | extra excludes, ORed with the internal-stack defaults |
| `CAUSA_SERVICE_NAME_LABEL_KEYS` | ordered label keys used to read a service name |
