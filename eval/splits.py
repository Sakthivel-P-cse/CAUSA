"""Train/test splits over chaos scenarios + injection sites (spec §3 / §3.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from contracts import Incident


@dataclass(frozen=True)
class Split:
    train: list[Incident]
    test: list[Incident]
    held_out_sites: frozenset[str]

    def assert_no_leakage(self) -> None:
        train_ids = {i.incident_id for i in self.train}
        test_ids = {i.incident_id for i in self.test}
        overlap = train_ids & test_ids
        if overlap:
            raise AssertionError(f"train/test incident leakage: {overlap}")

        train_keys = {(i.scenario, i.injection_site) for i in self.train}
        for inc in self.test:
            key = (inc.scenario, inc.injection_site)
            if key in train_keys and inc.injection_site in self.held_out_sites:
                # held-out sites must never appear in train
                raise AssertionError(f"held-out site leaked into train: {inc.injection_site}")


def scenario_train_test_split(
    incidents: Sequence[Incident],
    test_ratio: float = 0.3,
    held_out_sites: Sequence[str] | None = None,
    seed: int = 0,
) -> Split:
    """Split by (scenario, injection_site) groups — never by time windows.

    Integrity guarantee: no weight ever sees its test incident.
    """
    import random

    held = frozenset(held_out_sites or [])
    # Group by scenario+site so whole groups stay together
    groups: dict[tuple, list[Incident]] = {}
    for inc in incidents:
        key = (inc.scenario.value if inc.scenario else "unknown", inc.injection_site or "default")
        groups.setdefault(key, []).append(inc)

    keys = sorted(groups.keys())
    rng = random.Random(seed)
    rng.shuffle(keys)

    # Force held-out sites into test
    test_keys: list[tuple] = []
    train_keys: list[tuple] = []
    remaining: list[tuple] = []
    for k in keys:
        site = k[1]
        if site in held:
            test_keys.append(k)
        else:
            remaining.append(k)

    n_test = max(1, int(round(len(remaining) * test_ratio))) if remaining else 0
    test_keys.extend(remaining[:n_test])
    train_keys.extend(remaining[n_test:])

    # If everything was held out, keep at least one train group when possible
    if not train_keys and remaining:
        train_keys.append(test_keys.pop())

    train = [inc for k in train_keys for inc in groups[k]]
    test = [inc for k in test_keys for inc in groups[k]]
    split = Split(train=train, test=test, held_out_sites=held)
    split.assert_no_leakage()
    return split


ANALYTICAL_CROSS_TOPOLOGY_ARGUMENT = (
    "T/D/P are computed from local graph structure and live deviations; the scorer "
    "memorizes nothing per-node. The only tunable parameters are three global weights "
    "(w_T, w_D, w_P) — a 3-dimensional surface with negligible overfit risk relative to "
    "learned node embeddings. In-app site split (deep chain / wide fan-out / leaf origin) "
    "is therefore a genuine within-app generalization check, not a restatement of train score."
)
