"""
run_scenario.py — the ground-truth harness (contract C5, Dev_A brief §2).

Wraps one chaos run end-to-end:
    apply CRD → WAIT until the fault is actually injected → stamp inject_start
             → hold → stamp inject_end → tear down
             → write Incident{incident_id, true_root_node, fault_class, ...}

Key guarantees (vs the naive apply→sleep→delete→stamp):
  • The inject window brackets the ACTUAL fault (confirmed AllInjected), not the
    apply/delete wall-clock wrapper — so detection-latency isn't inflated by CRD
    admission + chaos propagation lag.
  • A run whose fault never activated does NOT produce a ground-truth row (no
    phantom incidents entering the corpus as "real"); override with
    --allow-unverified only when you know status polling is unavailable.
  • S0's real fault is the flagd `productCatalogFailure` flag, not a CRD — the
    harness flips that flag itself (and resets it on teardown). Because a flagd
    flip has no AllInjected object to poll, S0 records ONLY under --verify-effect
    (confirm an observable effect) or --allow-unverified, so the ineffective
    HTTPChaos stand-in can never mint a phantom S0 (audit M2).
  • S5's infra root is the CONCRETE node name (resolved from --node or the CRD's
    node selector), never the literal placeholder "NODE" — and when --node is
    given the APPLIED CRD's node selector is patched to that same node, so the
    node actually stressed always equals the recorded true_root_node.
  • --dry-run neither sleeps nor persists — it just prints what would run.

The Incident is written OUT-OF-BAND (a JSON file and/or the role-isolated
Postgres `groundtruth.ground_truth` table) — the RCA engine never reads it before
its output is scored. The CRD *is* the ground truth; nothing is hand-tuned.

Usage:
    python -m testbed.harness.run_scenario --list
    python -m testbed.harness.run_scenario --scenario S2 --hold-seconds 300 --out gt/
    python -m testbed.harness.run_scenario --scenario S5 --node kind-worker --write-db
    python -m testbed.harness.run_scenario --scenario S0 --verify-effect --out gt/
    python -m testbed.harness.run_scenario --scenario S1 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contracts import Incident, Window

from .catalog import Scenario, all_scenarios, get

logger = logging.getLogger("causa.testbed")

# Placeholder true-root in the catalog/CRD for infra scenarios; the harness
# substitutes the concrete node name at run time.
_NODE_SENTINEL = "NODE"

# flagd (S0) automation — the OTel Demo's feature-flag ConfigMap and the JSON
# flag-definition file it holds (audit M2). Overridable from the CLI.
_FLAGD_CONFIGMAP = "flagd-config"
_FLAGD_FILE_KEY = "demo.flagd.json"
_FLAGD_ON = "on"
_FLAGD_OFF = "off"

# --verify-effect defaults: a target counts as "actually broken" if its
# error_rate peaks above this fraction, or its p95_latency rises this many ×.
_DEFAULT_PROM_URL = "http://prometheus.prometheus.svc.cluster.local:9090"
_DEFAULT_ERROR_FLOOR = 0.05
_DEFAULT_LATENCY_RISE = 2.0


def _kubectl(args: list[str], context: str | None = None) -> None:
    cmd = ["kubectl"]
    if context:
        cmd += ["--context", context]
    cmd += args
    logger.info("$ %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


def _kubectl_stdin(args: list[str], manifest: str, context: str | None = None) -> None:
    """Like `_kubectl`, but feeds a rendered manifest to kubectl via stdin
    (`-f -`) instead of a file path — used when the CRD's node selector is
    patched at run time (S5 --node)."""
    cmd = ["kubectl"]
    if context:
        cmd += ["--context", context]
    cmd += args
    logger.info("$ %s   (manifest via stdin)", " ".join(cmd))
    subprocess.run(cmd, check=True, input=manifest, text=True)


def _kubectl_capture(args: list[str], context: str | None = None) -> str | None:
    """Run kubectl and return stdout, or None if it errors / is unavailable."""
    cmd = ["kubectl"]
    if context:
        cmd += ["--context", context]
    cmd += args
    try:
        res = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.debug("kubectl capture failed (%s): %s", " ".join(args), exc)
        return None
    return res.stdout


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _incident_id(scenario: Scenario, root: str, started: datetime) -> str:
    # Second-resolution stamp + a short random suffix so two runs of the same
    # scenario within one second can't collide (and silently overwrite via the
    # ON CONFLICT upsert).
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    return f"{scenario.id.lower()}-{root}-{stamp}-{uuid.uuid4().hex[:8]}"


# ── CRD parsing (no PyYAML dependency) ────────────────────────────────

def _crd_documents(path: Path) -> list[dict[str, object]]:
    """
    Minimal multi-doc parse of a Chaos-Mesh CRD file — enough to recover each
    object's kind, name, namespace, and node selector for status polling and
    infra-root resolution. Not a general YAML parser; it relies on the
    consistent 2-space-indented shape of these hand-authored CRDs.
    """
    docs: list[dict[str, object]] = []
    for chunk in re.split(r"(?m)^---\s*$", path.read_text()):
        if not chunk.strip():
            continue
        kind_m = re.search(r"(?m)^kind:\s*(.+?)\s*$", chunk)
        name_m = re.search(r"(?m)^\s{2}name:\s*(.+?)\s*$", chunk)
        ns_m = re.search(r"(?m)^\s{2}namespace:\s*(.+?)\s*$", chunk)
        nodes: list[str] = []
        nodes_block = re.search(r"(?m)^\s*nodes:\s*$((?:\n\s*-\s*.+)+)", chunk)
        if nodes_block:
            nodes = [
                n.strip().strip('"').strip("'")
                for n in re.findall(r"(?m)^\s*-\s*(.+?)\s*$", nodes_block.group(1))
            ]
        # inline-list form: nodes: ["a", "b"]
        if not nodes:
            inline = re.search(r"(?m)^\s*nodes:\s*\[(.+?)\]\s*$", chunk)
            if inline:
                nodes = [x.strip().strip('"').strip("'") for x in inline.group(1).split(",") if x.strip()]
        docs.append({
            "kind": kind_m.group(1).strip() if kind_m else "",
            "name": name_m.group(1).strip().strip('"').strip("'") if name_m else "",
            "namespace": ns_m.group(1).strip().strip('"').strip("'") if ns_m else "",
            "nodes": nodes,
        })
    return docs


def _node_from_crd(path: Path) -> str | None:
    for doc in _crd_documents(path):
        nodes = doc.get("nodes") or []
        if nodes:
            return str(nodes[0])
    return None


def _resolve_node(scenario: Scenario, node_name: str | None) -> str | None:
    """Concrete node name for an infra scenario: --node wins, else the CRD's."""
    return node_name or _node_from_crd(scenario.crd_path)


def _render_crd(path: Path, node_override: str | None) -> str:
    """
    CRD manifest text with its node selector repointed to `node_override`.

    Without an override the file is returned VERBATIM (multi-doc D1–D3 files pass
    through untouched — the regex only runs when we actually repoint a node). With
    an override, every `nodes:` selector (block or inline form, mirroring
    `_crd_documents`) is rewritten to the single override node, so the pod set that
    is actually stressed matches the node recorded as `true_root_node`. Text-level
    substitution keeps the no-PyYAML-dependency contract.
    """
    text = path.read_text()
    if not node_override:
        return text

    def _replace_block(m: re.Match) -> str:
        indent = m.group(1)                       # indent of the `nodes:` key
        item_m = re.match(r"([ \t]*)-", m.group(2))
        item_indent = item_m.group(1) if item_m else indent + "  "
        return f'{indent}nodes:\n{item_indent}- "{node_override}"\n'

    text, n_block = re.subn(
        r"(?m)^([ \t]*)nodes:[ \t]*$\n((?:[ \t]*-[ \t]*.+\n?)+)",
        _replace_block,
        text,
    )
    text, n_inline = re.subn(
        r"(?m)^([ \t]*)nodes:[ \t]*\[.*?\][ \t]*$",
        lambda m: f'{m.group(1)}nodes: ["{node_override}"]',
        text,
    )
    if n_block + n_inline == 0:
        logger.warning(
            "--node %s given but %s has no `nodes:` selector to patch; "
            "applying the CRD unchanged.", node_override, path.name,
        )
    return text


# ── flagd feature-flag injection (S0) ─────────────────────────────────
# S0's real fault is a flagd flag, not a CRD. The stand-in HTTPChaos can report
# AllInjected while intercepting nothing (gRPC), so recording S0 on that signal
# is a phantom (audit M2). The harness flips the flag itself and confirms via
# --verify-effect instead.

def _read_flagd_variant(flag_file_json: str, flag_key: str) -> str | None:
    """The flag's current defaultVariant, or None if the file/flag is absent."""
    try:
        flags = (json.loads(flag_file_json).get("flags") or {})
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    flag = flags.get(flag_key)
    if not isinstance(flag, dict) or flag.get("defaultVariant") is None:
        return None
    return str(flag["defaultVariant"])


def _set_flagd_variant(flag_file_json: str, flag_key: str, variant: str) -> str:
    """
    Return the flagd flag-definition JSON with `flag_key`'s defaultVariant set to
    `variant`. Raises KeyError if the flag (or, when the flag enumerates variants,
    the requested variant) is absent — so we never "inject" a flag the demo does
    not actually define, which would itself be a phantom.
    """
    doc = json.loads(flag_file_json)
    flags = doc.get("flags")
    if not isinstance(flags, dict) or not isinstance(flags.get(flag_key), dict):
        raise KeyError(f"flagd flag {flag_key!r} not found in the flag definition")
    variants = flags[flag_key].get("variants")
    if isinstance(variants, dict) and variant not in variants:
        raise KeyError(f"flagd flag {flag_key!r} has no variant {variant!r}")
    flags[flag_key]["defaultVariant"] = variant
    return json.dumps(doc, indent=2)


def _flagd_get_flagfile(
    namespace: str, context: str | None,
    configmap: str = _FLAGD_CONFIGMAP, key: str = _FLAGD_FILE_KEY,
) -> str | None:
    out = _kubectl_capture(["get", "configmap", configmap, "-n", namespace, "-o", "json"], context)
    if out is None:
        return None
    try:
        return json.loads(out).get("data", {}).get(key)
    except (json.JSONDecodeError, AttributeError):
        return None


def _flagd_patch_flagfile(
    new_flagfile: str, namespace: str, context: str | None,
    configmap: str = _FLAGD_CONFIGMAP, key: str = _FLAGD_FILE_KEY,
) -> bool:
    patch = json.dumps({"data": {key: new_flagfile}})
    try:
        _kubectl(["patch", "configmap", configmap, "-n", namespace, "--type", "merge", "-p", patch], context)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.error("flagd patch of %s failed: %s", configmap, exc)
        return False


# ── Observable-effect verification (--verify-effect) ──────────────────

def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _series_rose(series: list[list[float]], factor: float) -> bool:
    """True if a metric's peak is ≥ `factor`× its early-window (first-quartile
    median) level — a real, multiplicative rise, not baseline noise."""
    vals = [float(v) for _, v in series if v is not None]
    if len(vals) < 2:
        return False
    ref = _median(vals[: max(1, len(vals) // 4)])
    return ref > 0 and max(vals) >= factor * ref


def _effect_observed(
    adapter,
    nodes: list[str],
    window: Window,
    namespace: str | None = None,
    *,
    error_floor: float = _DEFAULT_ERROR_FLOOR,
    latency_rise: float = _DEFAULT_LATENCY_RISE,
) -> bool:
    """
    True if ANY target node shows an observable fault effect in `window`:
    error_rate peaking above `error_floor`, or p95_latency rising ≥`latency_rise`×
    its early-window level. This is the --verify-effect gate that keeps a phantom
    (injection admitted, nothing actually broke) out of the ground-truth corpus
    (audit M2). Most meaningful for service-level faults; infra/slow-burn
    scenarios may legitimately show no effect in a short probe.
    """
    for node in nodes:
        err = adapter.metrics(node, "error_rate", window, namespace=namespace)
        if err and max(float(v) for _, v in err) >= error_floor:
            return True
        lat = adapter.metrics(node, "p95_latency", window, namespace=namespace)
        if lat and _series_rose(lat, latency_rise):
            return True
    return False


def _injection_ok(
    allinjected: bool | None, effect_ok: bool | None, is_flagd: bool, allow_unverified: bool,
) -> bool:
    """
    Decide whether a run is a real, recordable incident.

      • --allow-unverified  → always record (operator override).
      • flagd (S0)          → no AllInjected signal exists, so require an OBSERVED
                              effect (effect_ok is True). Without --verify-effect
                              (effect_ok is None) the run is NOT recorded — this is
                              what stops S0's phantom.
      • CRD scenarios       → require AllInjected; if --verify-effect was also
                              requested, require the effect too.
    """
    if allow_unverified:
        return True
    if is_flagd:
        return effect_ok is True
    if not allinjected:
        return False
    return True if effect_ok is None else effect_ok


def _target_nodes(scenario: Scenario, node_name: str | None) -> list[str]:
    """The concrete node(s) to probe for an effect (infra sentinel resolved)."""
    resolved = _resolve_node(scenario, node_name)
    return [
        resolved if f.true_root_node == _NODE_SENTINEL and resolved else f.true_root_node
        for f in scenario.faults
    ]


def _build_incidents(
    scenario: Scenario,
    inject_start: datetime,
    inject_end: datetime,
    node_name: str | None,
) -> list[Incident]:
    resolved_node = _resolve_node(scenario, node_name)
    incidents: list[Incident] = []
    for fault in scenario.faults:
        root = fault.true_root_node
        if root == _NODE_SENTINEL:
            if not resolved_node:
                raise ValueError(
                    f"{scenario.id} has an infra root placeholder '{_NODE_SENTINEL}' "
                    f"but no node could be resolved — pass --node <name> or set a "
                    f"node selector in {scenario.crd_file}."
                )
            root = resolved_node
        incidents.append(
            Incident(
                incident_id=_incident_id(scenario, root, inject_start),
                true_root_node=root,
                fault_class=fault.fault_class,
                inject_start=inject_start,
                inject_end=inject_end,
            )
        )
    return incidents


# ── Injection verification ────────────────────────────────────────────

def _is_injected(status_json: str) -> bool:
    """True when a chaos object's status reports the fault fully injected."""
    try:
        status = json.loads(status_json).get("status", {})
    except (json.JSONDecodeError, AttributeError):
        return False
    for cond in status.get("conditions", []):
        if cond.get("type") == "AllInjected" and str(cond.get("status")) == "True":
            return True
    return False


def _wait_injected(
    targets: list[dict[str, object]],
    namespace: str,
    context: str | None,
    timeout: int,
) -> bool:
    """
    Poll every chaos object in `targets` until ALL report AllInjected, or timeout.
    Returns False (rather than blocking the whole timeout) if kubectl is
    unavailable — the caller decides whether to proceed unverified.
    """
    deadline = time.monotonic() + timeout
    poll = 3
    while time.monotonic() < deadline:
        all_ok = True
        saw_kubectl = False
        for t in targets:
            kind, name = str(t.get("kind")), str(t.get("name"))
            ns = str(t.get("namespace") or namespace)
            if not kind or not name:
                continue
            out = _kubectl_capture(["get", kind, name, "-n", ns, "-o", "json"], context)
            if out is None:
                all_ok = False
                continue
            saw_kubectl = True
            if not _is_injected(out):
                all_ok = False
        if saw_kubectl and all_ok:
            return True
        if not saw_kubectl:
            # kubectl not usable at all (no cluster/binary) — don't spin.
            logger.warning("Cannot query chaos status (kubectl unavailable).")
            return False
        time.sleep(poll)
    return False


def run_scenario(
    scenario: Scenario,
    *,
    namespace: str = "otel-demo",
    hold_seconds: int = 300,
    context: str | None = None,
    out_dir: Path | None = None,
    write_db: bool = False,
    apply: bool = True,
    node_name: str | None = None,
    inject_timeout: int = 120,
    allow_unverified: bool = False,
    verify_effect: bool = False,
    probe_seconds: int = 30,
    prometheus_url: str = _DEFAULT_PROM_URL,
    error_floor: float = _DEFAULT_ERROR_FLOOR,
) -> list[Incident]:
    """Execute one scenario and return the Incident ground-truth row(s)."""
    is_flagd = scenario.special == "flagd"

    # ── dry-run: describe only, never sleep or persist ────────────────
    if not apply:
        now = _now()
        incidents = _build_incidents(scenario, now, now, node_name)
        how = f"flip flagd flag {scenario.flag!r}" if is_flagd else "apply CRD"
        logger.info(
            "[dry-run] would %s for %s (roots=%s) for %ds — not applying, not persisting.",
            how, scenario.id, [i.true_root_node for i in incidents], hold_seconds,
        )
        return incidents

    # ── Mechanism-specific apply / teardown / injection-confirm ───────
    # flagd (S0): flip the real feature flag; there is NO AllInjected object to
    # poll, so confirmation comes only from --verify-effect. CRD scenarios apply
    # the manifest and poll AllInjected as before.
    targets: list[dict[str, object]] = []
    if is_flagd:
        if not scenario.flag:
            raise ValueError(f"{scenario.id} is special='flagd' but names no `flag` to flip.")
        _original_variant: dict[str, str | None] = {"v": None}

        def _apply() -> None:
            flagfile = _flagd_get_flagfile(namespace, context)
            if flagfile is None:
                raise RuntimeError(
                    f"cannot read flagd ConfigMap {_FLAGD_CONFIGMAP!r} in {namespace} "
                    f"(kubectl unavailable?) — {scenario.id} needs it to inject."
                )
            _original_variant["v"] = _read_flagd_variant(flagfile, scenario.flag) or _FLAGD_OFF
            patched = _set_flagd_variant(flagfile, scenario.flag, _FLAGD_ON)
            if not _flagd_patch_flagfile(patched, namespace, context):
                raise RuntimeError(f"could not enable flagd flag {scenario.flag!r}")
            logger.info("flagd flag %s → %s (S0 injection)", scenario.flag, _FLAGD_ON)

        def _teardown() -> None:
            # Reset the flag to whatever it was before (best-effort).
            flagfile = _flagd_get_flagfile(namespace, context)
            if flagfile is None:
                logger.warning("could not re-read flagd ConfigMap to reset %s.", scenario.flag)
                return
            try:
                reset = _set_flagd_variant(flagfile, scenario.flag, _original_variant["v"] or _FLAGD_OFF)
            except KeyError:
                return
            _flagd_patch_flagfile(reset, namespace, context)
            logger.info("flagd flag %s reset → %s", scenario.flag, _original_variant["v"] or _FLAGD_OFF)
    else:
        # Object kind/name/namespace (used for status polling) are unaffected by a
        # node repoint, so targets come from the on-disk CRD either way.
        targets = _crd_documents(scenario.crd_path)
        # When --node is given, apply a manifest whose node selector is repointed to
        # that node (so we stress the node we record); otherwise apply the file as-is.
        manifest = _render_crd(scenario.crd_path, node_name) if node_name else None

        def _apply() -> None:
            if manifest is not None:
                _kubectl_stdin(["apply", "-f", "-", "-n", namespace], manifest, context)
            else:
                _kubectl(["apply", "-f", str(scenario.crd_path), "-n", namespace], context)

        def _teardown() -> None:
            if manifest is not None:
                _kubectl_stdin(
                    ["delete", "-f", "-", "-n", namespace, "--ignore-not-found"],
                    manifest, context,
                )
            else:
                _kubectl(
                    ["delete", "-f", str(scenario.crd_path), "-n", namespace, "--ignore-not-found"],
                    context,
                )

    if is_flagd and not (verify_effect or allow_unverified):
        logger.error(
            "%s is a flagd scenario with no AllInjected signal — pass --verify-effect "
            "to confirm an observable effect (or --allow-unverified to override). "
            "NOT running, to avoid recording a phantom incident (audit M2).",
            scenario.id,
        )
        return []

    inject_start: datetime | None = None
    inject_end: datetime | None = None
    try:
        # apply INSIDE the try so a partial multi-doc apply is still torn down.
        _apply()

        # CRD scenarios: poll AllInjected. flagd: no such object → None.
        allinjected: bool | None = None
        if not is_flagd:
            allinjected = _wait_injected(targets, namespace, context, inject_timeout)

        # --verify-effect: after a short settle, require an observable effect on a
        # target before this run counts as a real incident (audit M2).
        effect_ok: bool | None = None
        if verify_effect:
            logger.info("Probing %ds for an observable effect…", probe_seconds)
            time.sleep(probe_seconds)
            probe_window = Window(_now() - timedelta(seconds=max(probe_seconds, 60) * 2), _now())
            effect_ok = _probe_effect(
                _target_nodes(scenario, node_name), probe_window,
                namespace=namespace, prometheus_url=prometheus_url, error_floor=error_floor,
            )

        if not _injection_ok(allinjected, effect_ok, is_flagd, allow_unverified):
            logger.error(
                "%s not confirmed (allinjected=%s, effect=%s) — NOT recording a "
                "ground-truth incident (would be phantom). Re-run with "
                "--allow-unverified to override.",
                scenario.id, allinjected, effect_ok,
            )
            return []
        if allow_unverified and not (allinjected or effect_ok):
            logger.warning("%s unconfirmed; proceeding under --allow-unverified.", scenario.id)

        # Stamp the window around the CONFIRMED fault, not the apply/delete wrapper.
        inject_start = _now()
        logger.info("Holding %s for %ds (let Stage-1 alert + Stage-2 RCA run)…",
                    scenario.id, hold_seconds)
        time.sleep(hold_seconds)
        inject_end = _now()
    finally:
        # Always tear down, even on interrupt — a stuck fault poisons later runs.
        # (CRD delete, or flagd flag reset — whichever was applied.)
        _teardown()

    if inject_start is None or inject_end is None:
        return []

    incidents = _build_incidents(scenario, inject_start, inject_end, node_name)
    _persist(incidents, scenario, out_dir=out_dir, write_db=write_db)
    return incidents


def _probe_effect(
    nodes: list[str],
    window: Window,
    *,
    namespace: str | None,
    prometheus_url: str,
    error_floor: float,
) -> bool:
    """Build a Prometheus-backed adapter and check for an observable effect on
    `nodes`. Returns False if Prometheus is unreachable (the caller then treats
    the run as unconfirmed). Kept thin so `_effect_observed` stays unit-testable
    with a fake adapter."""
    from ingest.adapters import TelemetryAdapter  # lazy: keeps the harness import light

    adapter = TelemetryAdapter.from_defaults(prometheus_url=prometheus_url, namespace=namespace)
    try:
        return _effect_observed(adapter, nodes, window, namespace, error_floor=error_floor)
    except Exception as exc:  # network/backend error → unconfirmed, not a crash
        logger.error("effect probe failed: %s", exc)
        return False
    finally:
        adapter.close()


def _persist(
    incidents: list[Incident],
    scenario: Scenario,
    *,
    out_dir: Path | None,
    write_db: bool,
) -> None:
    for inc in incidents:
        record = asdict(inc)
        record["inject_start"] = inc.inject_start.isoformat()
        record["inject_end"] = inc.inject_end.isoformat()
        record["scenario"] = scenario.id
        record["dual_group"] = scenario.id if scenario.dual else None

        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{inc.incident_id}.json"
            path.write_text(json.dumps(record, indent=2))
            logger.info("Wrote ground truth → %s", path)

        if write_db:
            from ingest.trigger.state import IncidentStore
            IncidentStore().write_ground_truth(inc)
            logger.info("Wrote ground truth → Postgres groundtruth.ground_truth (%s)",
                        inc.incident_id)

    if out_dir is None and not write_db:
        # Default: print so a run is never silently lost.
        for inc in incidents:
            print(json.dumps({**asdict(inc),
                              "inject_start": inc.inject_start.isoformat(),
                              "inject_end": inc.inject_end.isoformat()}, indent=2))


def _print_catalog() -> None:
    print(f"{'ID':4} {'stresses':32} true_root(s) / fault_class")
    print("-" * 78)
    for s in all_scenarios():
        roots = ", ".join(f"{f.true_root_node}:{f.fault_class}" for f in s.faults)
        print(f"{s.id:4} {s.stresses:32} {roots}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Causa chaos ground-truth harness")
    p.add_argument("--scenario", help="scenario id (S0–S5, D1–D3)")
    p.add_argument("--namespace", default="otel-demo")
    p.add_argument("--hold-seconds", type=int, default=300)
    p.add_argument("--context", default=None, help="kubectl context")
    p.add_argument("--node", default=None,
                   help="concrete node name for infra scenarios (S5); overrides the CRD selector")
    p.add_argument("--inject-timeout", type=int, default=120,
                   help="seconds to wait for AllInjected before treating the run as failed")
    p.add_argument("--allow-unverified", action="store_true",
                   help="record a ground-truth row even if injection can't be confirmed")
    p.add_argument("--verify-effect", action="store_true",
                   help="require an observable metric effect on the target before recording "
                        "(required for flagd scenarios like S0 to avoid phantom incidents)")
    p.add_argument("--probe-seconds", type=int, default=30,
                   help="settle time before the --verify-effect probe reads metrics")
    p.add_argument("--prometheus-url", default=_DEFAULT_PROM_URL,
                   help="Prometheus base URL for --verify-effect")
    p.add_argument("--error-floor", type=float, default=_DEFAULT_ERROR_FLOOR,
                   help="min peak error_rate fraction counted as an effect (--verify-effect)")
    p.add_argument("--out", type=Path, default=None, help="dir for ground-truth JSON")
    p.add_argument("--write-db", action="store_true", help="write to Postgres groundtruth schema")
    p.add_argument("--dry-run", action="store_true", help="don't apply/delete CRDs; print only")
    p.add_argument("--list", action="store_true", help="print the catalog and exit")
    args = p.parse_args(argv)

    if args.list or not args.scenario:
        _print_catalog()
        return 0

    scenario = get(args.scenario)
    run_scenario(
        scenario,
        namespace=args.namespace,
        hold_seconds=args.hold_seconds,
        context=args.context,
        out_dir=args.out,
        write_db=args.write_db,
        apply=not args.dry_run,
        node_name=args.node,
        inject_timeout=args.inject_timeout,
        allow_unverified=args.allow_unverified,
        verify_effect=args.verify_effect,
        probe_seconds=args.probe_seconds,
        prometheus_url=args.prometheus_url,
        error_floor=args.error_floor,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
