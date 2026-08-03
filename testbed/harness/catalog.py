"""
catalog.py — the chaos scenario catalog (RCA_Engine_Spec §4/§7).

Maps each scenario id → its CRD file, the KNOWN true root(s), and fault class.
The CRD *is* the ground truth (PRD2 §6 anti-circularity) — nothing here is
hand-tuned to make a scenario pass. Pure data, unit-testable without a cluster.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CHAOS_DIR = Path(__file__).resolve().parent.parent / "chaos"


@dataclass(frozen=True)
class Fault:
    """One injected fault (a dual scenario has two)."""

    true_root_node: str
    fault_class: str


@dataclass(frozen=True)
class Scenario:
    id: str
    crd_file: str
    stresses: str
    faults: tuple[Fault, ...]
    # S0's canonical form is a flagd feature flag, not a Chaos Mesh CRD.
    special: str | None = None
    # Dual-injection scenarios return >1 fault and share a group id.
    dual: bool = field(default=False)

    @property
    def crd_path(self) -> Path:
        return CHAOS_DIR / self.crd_file

    @property
    def true_roots(self) -> list[str]:
        return [f.true_root_node for f in self.faults]


CATALOG: dict[str, Scenario] = {
    "S0": Scenario("S0", "S0_productcatalog_failure.yaml", "sanity (control)",
                   (Fault("product-catalog", "AppLogic"),), special="flagd"),
    "S1": Scenario("S1", "S1_valkey_memory_slowburn.yaml", "T / slow-burn leaf",
                   (Fault("valkey-cart", "Resource"),)),
    "S2": Scenario("S2", "S2_retry_storm_delay.yaml", "edge orientation / D",
                   (Fault("product-catalog", "Dependency"),)),
    "S3": Scenario("S3", "S3_clock_skew.yaml", "T / causality ordering",
                   (Fault("checkout", "Timing"),)),
    "S4": Scenario("S4", "S4_kafka_shared_dep.yaml", "shared-dependency confound",
                   (Fault("kafka", "Dependency"),)),
    "S5": Scenario("S5", "S5_node_stress.yaml", "cross-layer arbitration",
                   (Fault("NODE", "Infra"),)),
    "D1": Scenario("D1", "D1_disjoint.yaml", "clean split (Stage 1)",
                   (Fault("currency", "Dependency"), Fault("email", "Resource")), dual=True),
    "D2": Scenario("D2", "D2_shared_dep.yaml", "false-merge handling (Stage 3)",
                   # Both call product-catalog (the shared downstream) — unlike
                   # `ad`, which is a leaf and shares no dependency.
                   (Fault("recommendation", "Resource"), Fault("checkout", "Resource")), dual=True),
    "D3": Scenario("D3", "D3_near_simultaneous.yaml", "abstention (onsets within Δ)",
                   (Fault("payment", "Dependency"), Fault("shipping", "Dependency")), dual=True),
}


def get(scenario_id: str) -> Scenario:
    key = scenario_id.strip().upper()
    if key not in CATALOG:
        raise KeyError(f"unknown scenario {scenario_id!r}; known: {sorted(CATALOG)}")
    return CATALOG[key]


def all_scenarios() -> list[Scenario]:
    return list(CATALOG.values())
