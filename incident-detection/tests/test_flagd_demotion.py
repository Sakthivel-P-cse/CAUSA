"""Control-plane services must be ranked by evidence, not by name."""

from __future__ import annotations

from contracts import CallType, NodeKind
from graphs.influence_graph import InfluenceGraph
from graphs.types import Edge, Node
from isolation.pipeline import _temporal_origins
from rca.types import KernelAnomaly
def test_temporal_origins_does_not_special_case_flagd():
    ig = InfluenceGraph(
        nodes={
            "flagd": Node(node_id="flagd", kind=NodeKind.SERVICE, name="flagd"),
            "cart": Node(node_id="cart", kind=NodeKind.SERVICE, name="cart"),
        },
        edges=[Edge(src="flagd", dst="cart", call_type=CallType.SYNC)],
    )
    anomaly = KernelAnomaly(
        anomaly_score={"flagd": 0.9, "cart": 0.8},
        first_deviation_time={"flagd": 0.0, "cart": 1.0},
        resource_deviation={},
    )
    origins = _temporal_origins({"flagd", "cart"}, anomaly, ig)
    assert origins == ["flagd"]
