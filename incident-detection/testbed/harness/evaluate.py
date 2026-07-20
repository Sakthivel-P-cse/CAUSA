"""
evaluate.py — offline accuracy harness over the kernel fixtures.

Runs each fixture through the PRODUCTION path (build_incident_subgraph →
isolate → merged ranking) and scores the result against its FixtureExpectation.
Supports weight sweeps and signal ablation so a scoring change can be measured
rather than argued about.

    python -m testbed.harness.evaluate                    # baseline
    python -m testbed.harness.evaluate --sweep            # weight grid
    python -m testbed.harness.evaluate --ablate           # signal subsets
    python -m testbed.harness.evaluate --weights T=.5,D=.3,P=.2

WHAT THIS MEASURES, AND WHAT IT DOES NOT
----------------------------------------
The fixtures are hand-authored, so this measures CONSISTENCY and REGRESSION —
"did a change move the numbers, and which way" — not absolute accuracy. A
fixture encodes its author's causal intuition; agreeing with it is not evidence
of being right about production. Absolute accuracy needs real chaos runs joined
to `groundtruth.ground_truth` through the `causa_eval` role, which is Dev C's
lane per DB_ROLE_CONTRACT.md.

Read RANK first. Top-1 over a handful of scenarios is a step function — most
weight changes show nothing until one flips — whereas MRR moves continuously and
shows the direction of travel.
"""

from __future__ import annotations

import argparse
import importlib
import itertools
import logging
import sys
from dataclasses import dataclass, field

from contracts import Role
from graphs.incident_subgraph import build_incident_subgraph
from isolation.pipeline import isolate

logger = logging.getLogger("causa.evaluate")

# Kernel fixtures. The IDs echo the chaos catalog's, but with ONE exception
# (S2, authored from its CRD) these are NOT models of those scenarios — they are
# abstract minimal graphs, each built to exercise one kernel mechanism, with
# invented node names and their own roots and fault classes. S0's fixture roots
# at `cart-db`/Dependency while chaos S0 targets `product-catalog`/AppLogic, and
# so on for S1, S5, D1, D2, D3. Do not read a result here as a statement about
# the corresponding chaos scenario. `--check-catalog` prints the divergence.
FIXTURES: tuple[tuple[str, str, str], ...] = (
    ("S0", "fixtures.incident_s0", "load_incident_s0"),
    ("S1", "fixtures.incident_s1", "load_incident_s1"),
    ("S2", "fixtures.incident_s2", "load_incident_s2"),
    ("S5", "fixtures.incident_s5", "load_incident_s5"),
    ("D1", "fixtures.incident_d1", "load_incident_d1"),
    ("D2", "fixtures.incident_d2", "load_incident_d2"),
    ("D3", "fixtures.incident_d3", "load_incident_d3"),
)

"""Chaos scenarios with no kernel fixture at all."""
CATALOGUED_WITHOUT_FIXTURE: tuple[str, ...] = ("S3", "S4")

SIGNALS = ("T", "D", "P")


@dataclass
class ScenarioResult:
    scenario: str
    true_root: str
    abstained: bool = False
    abstain_expected: bool = False
    predicted_root: str | None = None
    rank: int | None = None                  # 1-based position of the true root
    origins: list[str] = field(default_factory=list)
    origin_truth: list[str] = field(default_factory=list)
    cluster_count: int = 0
    cluster_count_expected: int = 1
    roots_in_distinct_clusters: bool | None = None
    cluster_tops: list[str] = field(default_factory=list)
    fault_class: str | None = None
    fault_class_truth: str | None = None

    @property
    def top1(self) -> bool:
        """Did the engine name A true root?

        NOT `predicted == true_root_node`. A dual scenario has two genuine roots
        and `true_root_node` designates one of them arbitrarily, so the strict
        form scores a coin flip between two equally-correct answers as a failure.
        For single-root scenarios `origin_truth` holds exactly one node and this
        is identical to the strict comparison.
        """
        if not self.origin_truth:
            return self.predicted_root == self.true_root
        return self.predicted_root in self.origin_truth

    @property
    def roots_found(self) -> float | None:
        """Fraction of true roots that top their own cluster.

        The metric that actually matters for a multi-fault incident: finding one
        of two faults is half a result, and top-1 alone cannot express that.
        """
        if not self.origin_truth:
            return None
        return sum(r in self.cluster_tops for r in self.origin_truth) / len(
            self.origin_truth
        )

    @property
    def reciprocal_rank(self) -> float:
        return 1.0 / self.rank if self.rank else 0.0

    @property
    def origin_precision(self) -> float | None:
        if not self.origins:
            return None
        return sum(o in self.origin_truth for o in self.origins) / len(self.origins)

    @property
    def origin_recall(self) -> float | None:
        if not self.origin_truth:
            return None
        return sum(t in self.origins for t in self.origin_truth) / len(self.origin_truth)

    @property
    def origin_exact(self) -> bool:
        return sorted(self.origins) == sorted(self.origin_truth)

    @property
    def cluster_count_ok(self) -> bool:
        return self.cluster_count == self.cluster_count_expected

    @property
    def abstention_ok(self) -> bool:
        return self.abstained == self.abstain_expected

    @property
    def fault_class_ok(self) -> bool | None:
        """None when the kernel declined to classify — a miss, not an error."""
        if self.fault_class is None:
            return None
        return self.fault_class == self.fault_class_truth


def evaluate_scenario(
    scenario: str,
    module_path: str,
    fn_name: str,
    *,
    weights: dict[str, float] | None = None,
    enabled_signals: set[str] | None = None,
) -> ScenarioResult:
    topology, traces, anomaly, truth = getattr(
        importlib.import_module(module_path), fn_name
    )()
    subgraph = build_incident_subgraph(topology, traces, anomaly)
    isolated = isolate(
        subgraph,
        anomaly=anomaly,
        weights=weights,
        enabled_signals=enabled_signals,
    )

    result = ScenarioResult(
        scenario=scenario,
        true_root=truth.true_root_node,
        abstain_expected=truth.ambiguous,
        origin_truth=sorted(truth.origin_nodes),
        cluster_count_expected=truth.expected_cluster_count,
        fault_class_truth=truth.fault_class,
    )

    if isolated.abstain_reason:
        result.abstained = True
        return result

    result.cluster_count = len(isolated.ranked_by_cluster)
    result.cluster_tops = [
        ranked.candidates[0].node_id
        for ranked in isolated.ranked_by_cluster.values()
        if ranked.candidates
    ]

    # Merge exactly as the orchestrator does, then rank.
    merged = [
        c for ranked in isolated.ranked_by_cluster.values() for c in ranked.candidates
    ]
    merged.sort(key=lambda c: (-c.score, c.node_id))
    if merged:
        result.predicted_root = merged[0].node_id
        # Rank of the BEST-placed true root, for the same reason top1 accepts
        # any true root: a dual scenario's `true_root_node` designates one of two
        # equals, so ranking the other one first is not a miss.
        targets = set(result.origin_truth) or {truth.true_root_node}
        for position, candidate in enumerate(merged, start=1):
            if candidate.node_id in targets:
                result.rank = position
                break

    result.origins = sorted(
        c.node_id for c in merged if c.role is Role.ORIGIN
    )
    # fault_class is a per-cluster verdict; take the cluster holding the top
    # candidate, which is what the report persists.
    for ranked in isolated.ranked_by_cluster.values():
        if ranked.candidates and ranked.candidates[0].node_id == result.predicted_root:
            result.fault_class = ranked.fault_class
            break

    # Cluster MEMBERSHIP, not just count: a dual incident must put its two true
    # roots in DIFFERENT clusters. D2 returns the right count while placing the
    # shared dependency alongside one root, which a count-only check cannot see.
    if len(result.origin_truth) > 1:
        placement = {}
        for cid, ranked in isolated.ranked_by_cluster.items():
            for candidate in ranked.candidates:
                if candidate.node_id in result.origin_truth:
                    placement.setdefault(candidate.node_id, cid)
        result.roots_in_distinct_clusters = (
            len(set(placement.values())) == len(result.origin_truth)
            if len(placement) == len(result.origin_truth)
            else False
        )

    return result


def run_all(
    *,
    weights: dict[str, float] | None = None,
    enabled_signals: set[str] | None = None,
) -> list[ScenarioResult]:
    return [
        evaluate_scenario(
            sid, mod, fn, weights=weights, enabled_signals=enabled_signals
        )
        for sid, mod, fn in FIXTURES
    ]


# ── reporting ─────────────────────────────────────────────────────────


def _fmt_opt(value: float | None) -> str:
    return "  -  " if value is None else f"{value:.2f} "


def summarize(results: list[ScenarioResult]) -> dict[str, float]:
    """Aggregates. Scenarios that correctly abstain are excluded from ranking
    metrics — there is no ranked list to be right about — but their abstention
    is scored separately so a silently-abstaining engine cannot look perfect."""
    ranked = [r for r in results if not r.abstained]
    n = len(ranked) or 1
    classified = [r for r in results if r.fault_class is not None]
    return {
        "top1": sum(r.top1 for r in ranked) / n,
        "roots_found": sum(r.roots_found or 0.0 for r in ranked) / n,
        "mrr": sum(r.reciprocal_rank for r in ranked) / n,
        "origin_exact": sum(r.origin_exact for r in ranked) / n,
        "origin_precision": sum(r.origin_precision or 0.0 for r in ranked) / n,
        "origin_recall": sum(r.origin_recall or 0.0 for r in ranked) / n,
        "cluster_count": sum(r.cluster_count_ok for r in ranked) / n,
        "abstention": sum(r.abstention_ok for r in results) / (len(results) or 1),
        "fault_class": (
            sum(bool(r.fault_class_ok) for r in classified) / len(classified)
            if classified else 0.0
        ),
        "fault_class_coverage": len(classified) / (len(results) or 1),
        "scored": len(ranked),
    }


def print_report(results: list[ScenarioResult]) -> None:
    print(f"{'scn':4s} {'top1':>4s} {'rank':>4s} {'origins':>7s} "
          f"{'prec':>5s} {'rec':>5s} {'clust':>5s} {'fault_class':>18s}")
    print("-" * 62)
    for r in results:
        if r.abstained:
            verdict = "OK" if r.abstention_ok else "UNEXPECTED"
            print(f"{r.scenario:4s} {'abstained — ' + verdict:>50s}")
            continue
        fc = (
            f"{r.fault_class}=={r.fault_class_truth}" if r.fault_class_ok
            else f"{r.fault_class or 'none'}!={r.fault_class_truth}"
        )
        print(
            f"{r.scenario:4s} "
            f"{'Y' if r.top1 else 'N':>4s} "
            f"{r.rank if r.rank else '-':>4} "
            f"{'exact' if r.origin_exact else 'off':>7s} "
            f"{_fmt_opt(r.origin_precision):>5s} "
            f"{_fmt_opt(r.origin_recall):>5s} "
            f"{str(r.cluster_count) + '/' + str(r.cluster_count_expected):>5s} "
            f"{fc:>18s}"
        )
        if not r.top1:
            print(f"       predicted {r.predicted_root!r}, truth {r.true_root!r}")
        if not r.origin_exact:
            print(f"       origins {r.origins} vs truth {r.origin_truth}")
        if r.roots_in_distinct_clusters is False:
            print("       true roots NOT separated into distinct clusters")

    s = summarize(results)
    print("-" * 62)
    print(f"scored {int(s['scored'])}/{len(results)}  "
          f"top1={s['top1']:.2f}  roots_found={s['roots_found']:.2f}  "
          f"MRR={s['mrr']:.3f}  origin_exact={s['origin_exact']:.2f} "
          f"(P={s['origin_precision']:.2f} R={s['origin_recall']:.2f})")
    print(f"cluster_count={s['cluster_count']:.2f}  "
          f"abstention={s['abstention']:.2f}  "
          f"fault_class={s['fault_class']:.2f} "
          f"(coverage {s['fault_class_coverage']:.2f})")
    print(
        "\nSCOPE: these are KERNEL fixtures — abstract graphs exercising one "
        "mechanism each.\nExcept S2 they do NOT model the chaos scenario whose "
        f"ID they share ({'--check-catalog' } shows the divergence), so these "
        "numbers say\nnothing about accuracy on real incidents. "
        f"{', '.join(CATALOGUED_WITHOUT_FIXTURE)} have no fixture at all."
    )


def print_sweep(rows: list[tuple[str, dict[str, float]]]) -> None:
    print(f"{'config':28s} {'top1':>5s} {'MRR':>6s} {'orig':>5s} {'clust':>6s}")
    print("-" * 54)
    for label, s in rows:
        print(f"{label:28s} {s['top1']:5.2f} {s['mrr']:6.3f} "
              f"{s['origin_exact']:5.2f} {s['cluster_count']:6.2f}")


# ── CLI ───────────────────────────────────────────────────────────────


def print_catalog_divergence() -> None:
    """Show, per ID, whether the kernel fixture models the chaos scenario.

    Exists so the mismatch is inspectable rather than folklore: anyone reading a
    fixture named S1 will reasonably assume it models chaos scenario S1.
    """
    from testbed.harness.catalog import CATALOG

    print(f"{'id':4s} {'chaos CRD target':34s} {'kernel fixture':34s}")
    print("-" * 74)
    for sid, module_path, fn_name in FIXTURES:
        _, _, _, truth = getattr(importlib.import_module(module_path), fn_name)()
        scenario = CATALOG.get(sid)
        chaos = (
            ", ".join(f"{f.true_root_node}/{f.fault_class}" for f in scenario.faults)
            if scenario else "(not in catalog)"
        )
        kernel = f"{truth.true_root_node}/{truth.fault_class}"
        flag = "" if scenario and truth.true_root_node in scenario.true_roots else "  <-- diverges"
        print(f"{sid:4s} {chaos:34s} {kernel:34s}{flag}")
    for sid in CATALOGUED_WITHOUT_FIXTURE:
        scenario = CATALOG.get(sid)
        chaos = ", ".join(
            f"{f.true_root_node}/{f.fault_class}" for f in scenario.faults
        ) if scenario else "?"
        print(f"{sid:4s} {chaos:34s} {'(no fixture)':34s}  <-- unmeasured")


def _parse_weights(raw: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in raw.split(","):
        key, _, value = part.partition("=")
        key = key.strip().upper()
        if key not in SIGNALS:
            raise argparse.ArgumentTypeError(f"unknown signal {key!r}")
        out[key] = float(value)
    return out


def _weight_grid(step: float) -> list[dict[str, float]]:
    """Simplex grid over (T, D, P) summing to 1."""
    ticks = int(round(1.0 / step))
    grid = []
    for t, d in itertools.product(range(ticks + 1), repeat=2):
        p = ticks - t - d
        if p < 0:
            continue
        grid.append({"T": t / ticks, "D": d / ticks, "P": p / ticks})
    return grid


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Offline RCA accuracy harness")
    parser.add_argument("--weights", type=_parse_weights,
                        help="e.g. T=0.5,D=0.3,P=0.2 (default: equal thirds)")
    parser.add_argument("--sweep", action="store_true",
                        help="grid-search T/D/P weights on the simplex")
    parser.add_argument("--sweep-step", type=float, default=0.1)
    parser.add_argument("--ablate", action="store_true",
                        help="score every non-empty subset of {T, D, P}")
    parser.add_argument("--top", type=int, default=12,
                        help="rows to show for --sweep (ranked by MRR)")
    parser.add_argument("--check-catalog", action="store_true",
                        help="show where kernel fixtures diverge from the chaos CRDs")
    args = parser.parse_args(argv)

    if args.check_catalog:
        print_catalog_divergence()
        return 0

    if args.sweep:
        rows = []
        for weights in _weight_grid(args.sweep_step):
            summary = summarize(run_all(weights=weights))
            label = " ".join(f"{k}={weights[k]:.2f}" for k in SIGNALS)
            rows.append((label, summary))
        rows.sort(key=lambda r: (-r[1]["mrr"], -r[1]["top1"]))
        print(f"WEIGHT SWEEP — {len(rows)} configs, top {args.top} by MRR\n")
        print_sweep(rows[: args.top])
        return 0

    if args.ablate:
        rows = []
        for size in range(1, len(SIGNALS) + 1):
            for subset in itertools.combinations(SIGNALS, size):
                summary = summarize(run_all(enabled_signals=set(subset)))
                rows.append(("+".join(subset), summary))
        print("SIGNAL ABLATION — which signals carry the result\n")
        print_sweep(rows)
        return 0

    print_report(run_all(weights=args.weights))
    return 0


if __name__ == "__main__":
    sys.exit(main())
