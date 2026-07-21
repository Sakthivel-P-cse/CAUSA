"""HANDOFF → partner dev: paths that have NEVER run against real infrastructure.

Source of truth: docs/dev_c_handoff.md ("Two things that have NEVER run against
real infrastructure"). Both fail SILENTLY, not loudly — that is why prose in a
doc is not enough and these exist as executable targets instead.

Each test below is SKIPPED until you point it at real infra via an env var.
- Nothing here fails in normal CI, so it will not block the integration push.
- When you have fixed a path, run it with the env var set; a PASS is the proof.
- Do NOT delete a test to make it "green" — un-skip it by supplying the env var.

═══════════════════════════════════════════════════════════════════════════════
 THINGS THE OTHER DEV MUST ADDRESS
═══════════════════════════════════════════════════════════════════════════════

 BLOCKER — verified by the two tests in this file (both fail silently today):

 [ ] 1. Postgres `ON CONFLICT` clause in rca.store.insert_report
         `ON CONFLICT (incident_id) WHERE incident_id IS NOT NULL DO NOTHING`
         - Every store unit test uses a fake cursor, so this SQL has never been
           parsed by a real Postgres. If the partial unique index `uq_rca_incident_id`
           is missing or the clause is malformed, EVERY insert raises and NO report
           is ever written.
         - Action: apply the rca_reports schema (incl. `uq_rca_incident_id`) to a
           real DB, then run test #1 below with CAUSA_TEST_PG_DSN set.

 [ ] 2. Node-level PromQL: PrometheusClient.get_node_cpu_pct_series /
         get_node_memory_pct_series
         - respx-mocked only. A malformed query returns [] and is swallowed by
           `_range_safe`, so infra nodes score 0.0 — indistinguishable from the
           original "infra can't be root cause" bug these methods were added to fix.
         - If node-exporter has no `node` label, the client falls back to an
           `instance` regex resolved from `kube_node_info.internal_ip`. Confirm
           `kube_node_info` exposes `internal_ip`, or infra scoring stays inert.
         - Action: run test #2 below against a real Prometheus (PROMETHEUS_URL +
           CAUSA_TEST_NODE), then smoke-test end to end with
           testbed/chaos/S5_node_stress.yaml and confirm the report names the node
           with failure_type = 'Infra'.

 KNOWN GAPS — not unit-testable here (need the chaos corpus + ground truth);
 tracked so they are not forgotten. See docs/dev_c_handoff.md "Known gaps":

 [ ] 3. confidence_score is UNCALIBRATED (rank-margin, not a probability).
         Isotonic / ECE calibration is Dev C's lane.
 [ ] 4. failure_type coverage is PARTIAL — only Infra/Dependency/Resource are
         assigned from structure; AppLogic/Config/Data stay NULL. NULL means
         "not classified", never "no fault". Miss: an *instrumented* service that
         is a Dependency *fault* but not a NodeKind.DEPENDENCY *node* → NULL.
 [ ] 5. S3 (clock skew) and S4 (Kafka shared dependency) have chaos CRDs but no
         offline fixture — those paths are unexercised offline.
 [ ] 6. Untuned constants: T/D/P equal thirds, role thresholds 0.55/0.45/0.35,
         ALPHA=0.5, D_PRIOR=1.0, kappa=3.0 are reasoned defaults, not fitted.
         Fitting needs real chaos runs joined to ground truth (causa_eval lane).
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Env gates — set these to un-skip the corresponding test.
PG_DSN = os.getenv("CAUSA_TEST_PG_DSN")          # e.g. postgresql://user@localhost/causa_test
PROM_URL = os.getenv("PROMETHEUS_URL")           # e.g. http://localhost:9090
TEST_NODE = os.getenv("CAUSA_TEST_NODE")         # a real k8s node name Prometheus knows


# --------------------------------------------------------------------------- #
# BLOCKER #1 — the ON CONFLICT clause must parse AND be idempotent on real PG. #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not PG_DSN,
    reason="set CAUSA_TEST_PG_DSN to a throwaway DB with the rca_reports schema loaded "
    "(incl. uq_rca_incident_id) to verify the ON CONFLICT clause",
)
def test_on_conflict_clause_parses_and_is_idempotent():
    """The exact clause from rca.store must parse and DO NOTHING on the 2nd insert.

    Passing proves the partial unique index `uq_rca_incident_id` exists and the
    `ON CONFLICT (incident_id) WHERE incident_id IS NOT NULL` syntax is valid —
    the thing no fake-cursor unit test can prove.
    """
    import psycopg  # local import: only needed when this test actually runs

    incident_id = "handoff-onconflict-probe"
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        try:
            conn.execute(
                "DELETE FROM rca_reports WHERE incident_id = %s", (incident_id,)
            )
            insert = (
                "INSERT INTO rca_reports (alert_name, incident_id) VALUES (%s, %s) "
                "ON CONFLICT (incident_id) WHERE incident_id IS NOT NULL DO NOTHING"
            )
            first = conn.execute(insert, ("handoff-probe", incident_id))
            assert first.rowcount == 1, "first insert should write exactly one row"

            second = conn.execute(insert, ("handoff-probe", incident_id))
            # The whole point: the duplicate must be swallowed, not raise.
            assert second.rowcount == 0, "duplicate incident_id must affect 0 rows"
        finally:
            conn.execute(
                "DELETE FROM rca_reports WHERE incident_id = %s", (incident_id,)
            )


# --------------------------------------------------------------------------- #
# BLOCKER #2 — node PromQL must return a REAL, NON-EMPTY series.               #
#   Empty is the exact silent-failure signature (infra then scores 0.0).      #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not (PROM_URL and TEST_NODE),
    reason="set PROMETHEUS_URL and CAUSA_TEST_NODE (a real node name) to verify "
    "node CPU/memory PromQL returns data rather than silently empty",
)
def test_node_cpu_and_memory_series_are_nonempty():
    """get_node_cpu/memory_pct_series must return points for a real node.

    An empty list here is indistinguishable from the bug these methods fixed —
    so a non-empty series is the only acceptable proof the queries and the
    node/instance relabel fallback (kube_node_info.internal_ip) actually work.
    """
    from ingest.clients.prometheus_client import PrometheusClient

    client = PrometheusClient(base_url=PROM_URL)
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(minutes=10)

    cpu = client.get_node_cpu_pct_series(TEST_NODE, start, end)
    mem = client.get_node_memory_pct_series(TEST_NODE, start, end)

    assert cpu, f"CPU series for node {TEST_NODE!r} was empty — query or relabel is broken"
    assert mem, f"memory series for node {TEST_NODE!r} was empty — query or relabel is broken"
    # Shape sanity: each point is [timestamp, value].
    assert all(len(pt) == 2 for pt in cpu)
    assert all(len(pt) == 2 for pt in mem)
