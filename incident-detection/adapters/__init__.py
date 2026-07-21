"""Boundary adapters: external shapes → contract types.

Lanes must not import each other's implementations. Adapters sit at the edge
and translate fixture / A+B payloads into contracts.
"""

from adapters.anomaly import bag_from_fixture_nodes, bag_from_node_outputs
from adapters.isolation import IsolationView
from adapters.logs import (
    log_records_from_envelopes,
    messages_by_node,
    messages_from_fixture_logs,
)
from adapters.scorer import ABScorer
from adapters.subgraph import incident_subgraph_from_golden

__all__ = [
    "ABScorer",
    "IsolationView",
    "bag_from_fixture_nodes",
    "bag_from_node_outputs",
    "incident_subgraph_from_golden",
    "log_records_from_envelopes",
    "messages_by_node",
    "messages_from_fixture_logs",
]
