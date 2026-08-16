"""
contracts/anomaly.py — Causa contract C2: the anomaly primitives (AnomalyOutput).

These are the foundational per-node deviation primitives that ALL THREE scoring
signals (T, D, P) sit on top of (RCA_Engine_Spec §1.1, Team_Split §7.1). A bad
baseline poisons every signal at once — treat this contract as load-bearing.

stdlib-only, like every `contracts/` module.

Produced by:  anomaly/  (Dev A)
Consumed by:  Dev B (all scoring), Dev C (classification via dev_vector)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


# The metrics every node is scored on. One-sided: only INCREASES are anomalous.
# `retry_rate` and `saturation` may be unavailable per-node on some testbeds —
# anomaly_score's max-composite tolerates missing metrics; never synthesize them.
METRICS: tuple[str, ...] = (
    "p95_latency",
    "error_rate",
    "cpu",
    "retry_rate",
    "saturation",
)


@dataclass(frozen=True)
class Baseline:
    """
    Robust healthy-baseline statistics for one (node, metric), learned from a
    warmup window. `mad` is already scaled (1.4826 * MAD ≈ σ for normal data),
    so it is directly comparable to a standard deviation. Robust median/MAD
    means a spike inside the warmup window barely moves these numbers.
    """

    median: float
    mad: float          # = 1.4826 * median(|x - median|), floored at a small ε
    n: int              # sample count the baseline was learned from


@dataclass(frozen=True)
class Thresholds:
    """
    Firing + squash parameters (RCA_Engine_Spec §1.1). Defaults are the spec's.

      θ_fire     — anomaly_score above this counts as "deviating".
      k_persist  — consecutive windows above θ_fire before FirstDeviationTime fires
                   (guards against single-window blips).
      θ_low      — secondary threshold: "quiet but non-zero" (Dev B's subgraph
                   inclusion + isolation gap-repair, spec §7 Stage 2).
      kappa (κ)  — squash sensitivity in dev_m = 1 - exp(-z/κ).
    """

    theta_fire: float = 0.5
    k_persist: int = 3
    theta_low: float = 0.25
    kappa: float = 3.0


@dataclass
class AnomalyOutput:
    """
    Per-node anomaly summary (RCA_Engine_Spec §1.1, contract C2).

      anomaly_score   — max over metrics of dev_m ∈ [0,1]. Max (not weighted sum)
                        so one sharply-broken metric isn't diluted by healthy ones.
      dev_vector      — per-metric deviations, retained for Dev C's classification
                        + explainability. Missing metrics are simply absent.
      first_deviation — FirstDeviationTime: earliest window where anomaly_score
                        crossed θ_fire for k_persist consecutive windows, or None
                        for slow-burn nodes that legitimately never cross (spec
                        §1.1 known-gap; Dev C measures the null as subgraph recall).
                        NEVER faked to a value — the null is a feature.
      window_ts       — the window timestamp anomaly_score/dev_vector were computed
                        at (aware UTC); the peak window for a summarized node.
    """

    node: str
    anomaly_score: float
    dev_vector: dict[str, float] = field(default_factory=dict)
    first_deviation: datetime | None = None
    window_ts: datetime | None = None
