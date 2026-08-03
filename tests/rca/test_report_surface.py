"""
The columns Dev C reads. These are a cross-team contract, so their CONTENT has
to match their NAME.

Both `origin_candidates` and `blast_radius` previously received the entire
ranked candidate list: the first therefore contained nodes the engine had
concluded were victims, and the second was a verbatim copy of the first rather
than a radius. A consumer keying on either column name got the wrong set, and
nothing failed — which is exactly how it survived to be found by inspection.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from contracts import (
    AnomalyOutput,
    IncidentTelemetry,
    NodeKind,
    RankedResult,
    Role,
    RootCauseCandidate,
    SignalScores,
    Thresholds,
)
from rca.store import RCAStore

NOW = datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, rows, rowcount=None):
        self.rows = rows
        self.rowcount = len(rows) if rowcount is None else rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self):
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return _Cursor([])


def _candidate(node_id, score, role, anomaly):
    return RootCauseCandidate(
        node_id=node_id,
        node_kind=NodeKind.SERVICE,
        score=score,
        confidence=score / 2,
        role=role,
        signals=SignalScores(T=0.5, D=0.5, P=0.5),
        dev_vector={"error_rate": anomaly},
        explanation=f"{node_id}. Role: {role.value}.",
    )


class _Incident:
    incident_id = "inc-1"
    alert_name = "ServiceHighErrorRate"
    alerted_service = "frontend"
    namespace = "otel-demo"
    triggered_at = NOW
    resolved_at = None
    trace_id = "trace-1"


def _write():
    """One report with a full mix of roles and anomaly levels."""
    connection = _Connection()
    store = RCAStore(connect_factory=lambda: connection)

    candidates = [
        _candidate("root", 1.00, Role.ORIGIN, 0.90),
        _candidate("relay", 0.70, Role.AMPLIFIER, 0.80),
        _candidate("hit", 0.40, Role.VICTIM, 0.60),          # deviated
        _candidate("hosted", 0.30, Role.VICTIM_OF_INFRA, 0.55),  # deviated
        _candidate("bystander", 0.10, Role.UNKNOWN, 0.05),    # below theta_fire
    ]
    anomalies = [
        AnomalyOutput(node="root", anomaly_score=0.90, first_deviation=NOW, dev_vector={}),
        AnomalyOutput(
            node="relay",
            anomaly_score=0.80,
            first_deviation=NOW + timedelta(seconds=10),
            dev_vector={},
        ),
        AnomalyOutput(
            node="hit",
            anomaly_score=0.60,
            first_deviation=NOW + timedelta(seconds=20),
            dev_vector={},
        ),
        AnomalyOutput(
            node="hosted",
            anomaly_score=0.55,
            first_deviation=NOW + timedelta(seconds=25),
            dev_vector={},
        ),
        AnomalyOutput(node="bystander", anomaly_score=0.05, dev_vector={}),
    ]
    telemetry = IncidentTelemetry(
        incident_id="inc-1",
        alerted_service="frontend",
        window_start=NOW,
        window_end=NOW + timedelta(minutes=10),
    )
    store.insert_report(
        _Incident(),
        telemetry,
        anomalies,
        RankedResult(candidates=candidates, incident_id="inc-1"),
        Thresholds(),
    )

    sql, params = connection.executed[-1]
    # Column order in the INSERT: ... origin_candidates, amplifier_nodes,
    # blast_radius, anomaly_scores ...
    return {
        "origin_candidates": json.loads(params[7]),
        "amplifier_nodes": json.loads(params[8]),
        "blast_radius": json.loads(params[9]),
        "anomaly_scores": json.loads(params[10]),
        "timeline": json.loads(params[15]),
    }


def test_origin_candidates_excludes_known_effects():
    """A node concluded to be a victim is not a candidate CAUSE."""
    written = _write()
    ids = [c["node_id"] for c in written["origin_candidates"]]
    assert "hit" not in ids, "a victim was offered as a candidate cause"
    assert "hosted" not in ids, "a victim-of-infra was offered as a cause"
    assert ids == ["root", "relay", "bystander"]


def test_origin_candidates_keeps_ranked_alternatives():
    """Not narrowed to the single origin — Dev C needs the ranked alternatives
    with their scores to calibrate against."""
    written = _write()
    ids = [c["node_id"] for c in written["origin_candidates"]]
    assert len(ids) > 1
    scores = [c["score"] for c in written["origin_candidates"]]
    assert scores == sorted(scores, reverse=True)


def test_exactly_one_candidate_carries_the_origin_role():
    written = _write()
    origins = [
        c["node_id"] for c in written["origin_candidates"] if c["role"] == "origin"
    ]
    assert origins == ["root"]


def test_blast_radius_is_what_the_incident_reached_not_the_candidate_list():
    """Nodes at or above theta_fire, minus the cause. Previously a verbatim copy
    of the candidate list, which made it neither a radius nor a blast."""
    written = _write()
    assert written["blast_radius"] == ["hit", "hosted", "relay"]


def test_blast_radius_excludes_the_origin():
    written = _write()
    assert "root" not in written["blast_radius"], "the cause is not its own blast"


def test_blast_radius_excludes_nodes_below_theta_fire():
    """Connectivity-expanded neighbours were never impacted."""
    written = _write()
    assert "bystander" not in written["blast_radius"]


def test_blast_radius_and_origin_candidates_are_not_the_same_set():
    written = _write()
    assert set(written["blast_radius"]) != {
        c["node_id"] for c in written["origin_candidates"]
    }


def test_anomaly_scores_stays_unfiltered():
    """The raw per-node scores are the one column that should NOT be narrowed —
    Dev C needs the full picture to calibrate."""
    written = _write()
    assert set(written["anomaly_scores"]) == {
        "root", "relay", "hit", "hosted", "bystander"
    }


def test_amplifier_nodes_matches_the_role():
    written = _write()
    assert written["amplifier_nodes"] == ["relay"]


def test_timeline_carries_score_role_and_offsets():
    written = _write()
    timeline = written["timeline"]
    assert [e["service"] for e in timeline] == ["root", "relay", "hit", "hosted"]
    assert timeline[0]["offset_seconds"] == 0.0
    assert timeline[1]["offset_seconds"] == 10.0
    assert timeline[0]["role"] == "origin"
    assert timeline[1]["role"] == "amplifier"
    assert timeline[0]["anomaly_score"] == 0.9
    assert timeline[0]["ordering_source"] == "metric_onset"
    assert "bystander" not in [e["service"] for e in timeline]
