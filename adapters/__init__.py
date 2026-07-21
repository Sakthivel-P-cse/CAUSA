"""Boundary adapters: external shapes → contract types.

Lanes must not import each other's implementations. Adapters sit at the edge
of Dev C and translate fixture / future A+B payloads into contracts.
"""

from adapters.anomaly import bag_from_fixture_nodes, bag_from_node_outputs
from adapters.isolation import IsolationView
from adapters.logs import messages_by_node, messages_from_fixture_logs

__all__ = [
    "IsolationView",
    "bag_from_fixture_nodes",
    "bag_from_node_outputs",
    "messages_by_node",
    "messages_from_fixture_logs",
]
