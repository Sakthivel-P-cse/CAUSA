"""
Dispatcher idempotency + quiet-relay path search.

Both were defects where the failure mode is silent: a resent alert re-ran RCA
and wrote a duplicate report, and gap repair abandoned a valid quiet relay
because the first path it happened to try failed the time check.
"""

from __future__ import annotations

import threading
import time

from contracts import CallType, NodeKind
from graphs.influence_graph import InfluenceGraph
from graphs.types import Edge, Node
from isolation.pipeline import _path_exists_quiet_relay
from rca.trigger.app import Dispatcher
from rca.types import KernelAnomaly

THETA_LOW = 0.25


# ── dispatcher dedup ──────────────────────────────────────────────────


class _QueueStore:
    def __init__(self):
        self.pending = set()
        self.running = set()
        self.lock = threading.Lock()

    def enqueue_trigger(self, ids):
        with self.lock:
            self.pending.update(set(ids) - self.running)

    def process_trigger(self, process):
        with self.lock:
            if not self.pending:
                return False
            item = self.pending.pop()
            self.running.add(item)
        try:
            process(item)
        finally:
            with self.lock:
                self.running.remove(item)
        return True

    def wait(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self.lock:
                if not self.pending and not self.running:
                    return
            time.sleep(.01)
        raise AssertionError("queue did not drain")


class _BlockingOrchestrator:
    """Holds each incident inside process() until released."""

    def __init__(self):
        self.store = _QueueStore()
        self.processed: list[str] = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def process(self, incident_id: str) -> None:
        self.processed.append(incident_id)
        self.entered.set()
        self.release.wait(timeout=5)


def test_resent_alert_is_not_processed_twice():
    """Alertmanager resends a firing alert every repeat_interval."""
    orch = _BlockingOrchestrator()
    dispatcher = Dispatcher(orch, workers=1)

    assert dispatcher.submit(["inc-1"]) is True
    orch.entered.wait(timeout=5)
    # Resend while the first run is still in flight.
    assert dispatcher.submit(["inc-1"]) is True, "a resend must still be accepted"
    orch.release.set()
    orch.store.wait()

    assert orch.processed == ["inc-1"], f"processed twice: {orch.processed}"


def test_incident_can_be_reprocessed_after_completion():
    """Dedup covers in-flight work only — it must not permanently blacklist."""
    orch = _BlockingOrchestrator()
    orch.release.set()  # don't block
    dispatcher = Dispatcher(orch, workers=1)

    dispatcher.submit(["inc-2"])
    orch.store.wait()
    dispatcher.submit(["inc-2"])
    orch.store.wait()

    assert orch.processed == ["inc-2", "inc-2"]


def test_distinct_incidents_are_not_deduped_against_each_other():
    orch = _BlockingOrchestrator()
    orch.release.set()
    dispatcher = Dispatcher(orch, workers=1)

    dispatcher.submit(["inc-a", "inc-b", "inc-a"])
    orch.store.wait()

    assert sorted(orch.processed) == ["inc-a", "inc-b"]


# ── quiet-relay path search ───────────────────────────────────────────


def _graph(edges: list[tuple[str, str]]) -> InfluenceGraph:
    ids = {n for e in edges for n in e}
    return InfluenceGraph(
        nodes={i: Node(i, NodeKind.SERVICE, i) for i in ids},
        edges=[Edge(s, d, CallType.SYNC) for s, d in edges],
    )


def test_valid_relay_found_when_an_earlier_path_violates_time_order():
    """Two disjoint relays connect a→b. The lexically-first one breaks the
    non-decreasing time requirement; the other satisfies it. Marking nodes
    visited on enqueue meant only one path was ever examined, so the search
    aborted and gap repair under-merged."""
    ig = _graph([("a", "bad"), ("bad", "b"), ("a", "good"), ("good", "b")])
    anomaly = KernelAnomaly(
        # Both relays are quiet; only their deviation times differ.
        anomaly_score={"a": 0.9, "b": 0.9, "bad": 0.05, "good": 0.05},
        first_deviation_time={"a": 10.0, "bad": 5.0, "good": 20.0, "b": 30.0},
        resource_deviation={},
    )
    assert _path_exists_quiet_relay(ig, anomaly, "a", "b", THETA_LOW) is True


def test_noisy_relay_does_not_connect():
    ig = _graph([("a", "loud"), ("loud", "b")])
    anomaly = KernelAnomaly(
        anomaly_score={"a": 0.9, "b": 0.9, "loud": 0.80},  # > theta_low
        first_deviation_time={"a": 10.0, "loud": 20.0, "b": 30.0},
        resource_deviation={},
    )
    assert _path_exists_quiet_relay(ig, anomaly, "a", "b", THETA_LOW) is False


def test_time_inversion_alone_blocks_the_relay():
    ig = _graph([("a", "relay"), ("relay", "b")])
    anomaly = KernelAnomaly(
        anomaly_score={"a": 0.9, "b": 0.9, "relay": 0.05},
        first_deviation_time={"a": 30.0, "relay": 20.0, "b": 10.0},  # decreasing
        resource_deviation={},
    )
    assert _path_exists_quiet_relay(ig, anomaly, "a", "b", THETA_LOW) is False


def test_relay_without_deviation_times_does_not_constrain_order():
    """A quiet node with no FirstDeviationTime must pass the relay through."""
    ig = _graph([("a", "relay"), ("relay", "b")])
    anomaly = KernelAnomaly(
        anomaly_score={"a": 0.9, "b": 0.9, "relay": 0.05},
        first_deviation_time={"a": 10.0, "relay": None, "b": 30.0},
        resource_deviation={},
    )
    assert _path_exists_quiet_relay(ig, anomaly, "a", "b", THETA_LOW) is True


def test_cycle_does_not_hang_the_search():
    ig = _graph([("a", "r1"), ("r1", "r2"), ("r2", "r1"), ("r2", "b")])
    anomaly = KernelAnomaly(
        anomaly_score={"a": 0.9, "b": 0.9, "r1": 0.05, "r2": 0.05},
        first_deviation_time={"a": 10.0, "r1": 15.0, "r2": 20.0, "b": 30.0},
        resource_deviation={},
    )
    assert _path_exists_quiet_relay(ig, anomaly, "a", "b", THETA_LOW) is True


def test_disconnected_nodes_are_not_linked():
    ig = _graph([("a", "x"), ("b", "y")])
    anomaly = KernelAnomaly(
        anomaly_score={"a": 0.9, "b": 0.9, "x": 0.05, "y": 0.05},
        first_deviation_time={"a": 10.0, "x": 15.0, "y": 20.0, "b": 30.0},
        resource_deviation={},
    )
    assert _path_exists_quiet_relay(ig, anomaly, "a", "b", THETA_LOW) is False
