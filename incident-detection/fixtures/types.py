"""Kernel-fixture expectations; not a production or cross-team contract."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FixtureExpectation:
    incident_id: str
    true_root_node: str
    fault_class: str
    inject_start: float
    inject_end: float
    expected_cluster_count: int = 1
    origin_nodes: frozenset[str] = field(default_factory=frozenset)
    ambiguous: bool = False
