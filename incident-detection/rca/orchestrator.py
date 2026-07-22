"""Stage-2 orchestration from persisted incident state to a C3 RCA result."""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import timedelta
from typing import Callable, Mapping

from anomaly import first_deviation_from_scores, score_values
from contracts import AnomalyOutput, Baseline, IncidentTelemetry, RankedResult, Thresholds
from graphs import build_incident_subgraph, build_influence_graph
from ingest.reader import TelemetryReader
from isolation import isolate
from scoring.fault_class import derive_fault_class

from .adapter import adapt_incident
from .store import IncidentState, RCAStore

logger = logging.getLogger(__name__)

MISSING_BASELINES_REASON = "missing_anomaly_baselines"
_missing_baselines_total = 0


def _default_reader_factory() -> TelemetryReader:
    import os

    return TelemetryReader.from_defaults(
        prometheus_url=os.getenv(
            "PROMETHEUS_URL", "http://prometheus.prometheus.svc.cluster.local:9090"
        ),
        tempo_url=os.getenv("TEMPO_URL", "http://tempo.tempo.svc.cluster.local:3200"),
        loki_url=os.getenv("LOKI_URL", "http://loki.loki.svc.cluster.local:3100"),
    )


def anomalies_from_telemetry(
    telemetry: IncidentTelemetry,
    baselines: Mapping[tuple[str, str], Baseline],
    thresholds: Thresholds | None = None,
) -> list[AnomalyOutput]:
    """Compute canonical C2 summaries from the already-loaded C1 metric series."""
    thresholds = thresholds or Thresholds()
    values: dict[str, dict[float, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    nodes = {telemetry.alerted_service}

    for point in telemetry.metric_points:
        nodes.add(point.service)
        samples = point.series or [
            [
                (point.timestamp or telemetry.window_end).timestamp(),
                point.value,
            ]
        ]
        for timestamp, value in samples:
            values[point.service][float(timestamp)][point.metric] = float(value)

    outputs: list[AnomalyOutput] = []
    for node in sorted(nodes):
        series = [
            score_values(
                node,
                metric_values,
                baselines,
                thresholds,
                window_ts=_timestamp(timestamp),
            )
            for timestamp, metric_values in sorted(values.get(node, {}).items())
        ]
        first = first_deviation_from_scores(
            [
                (output.window_ts, output.anomaly_score)
                for output in series
                if output.window_ts is not None
            ],
            thresholds,
        )
        if series:
            peak = max(series, key=lambda output: output.anomaly_score)
            outputs.append(
                AnomalyOutput(
                    node=node,
                    anomaly_score=peak.anomaly_score,
                    dev_vector=peak.dev_vector,
                    first_deviation=first,
                    window_ts=peak.window_ts,
                )
            )
        else:
            outputs.append(AnomalyOutput(node=node, anomaly_score=0.0))
    return outputs


def _timestamp(value: float):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value, tz=timezone.utc)


class RCAOrchestrator:
    """Load an incident, run the A-to-B adapter/kernel, and persist its report."""

    def __init__(
        self,
        store: RCAStore,
        reader_factory: Callable[[], TelemetryReader] = _default_reader_factory,
        *,
        thresholds: Thresholds | None = None,
    ):
        self.store = store
        self.reader_factory = reader_factory
        self.thresholds = thresholds or Thresholds()

    def process(self, incident_id: str) -> RankedResult:
        global _missing_baselines_total
        state = self.store.load_incident(incident_id)
        baselines = self.store.load_baselines()
        if not baselines:
            _missing_baselines_total += 1
            logger.error(
                "anomaly_baseline empty for incident %s — refusing quiet abstain "
                "(missing_baselines_total=%d)",
                incident_id,
                _missing_baselines_total,
            )
            result = RankedResult(
                candidates=[],
                incident_id=incident_id,
                abstain_reason=MISSING_BASELINES_REASON,
            )
            telemetry = IncidentTelemetry(
                incident_id=incident_id,
                alerted_service=state.alerted_service or "unknown",
                window_start=state.window_start,
                window_end=state.window_end,
            )
            self.store.insert_report(
                state, telemetry, [], result, self.thresholds
            )
            return result

        telemetry = self._load_telemetry(state)
        anomalies = anomalies_from_telemetry(telemetry, baselines, self.thresholds)
        kernel_input = adapt_incident(
            telemetry, anomalies, thresholds=self.thresholds
        )
        subgraph = build_incident_subgraph(
            kernel_input.topology,
            kernel_input.traces,
            kernel_input.anomaly,
            theta_fire=self.thresholds.theta_fire,
            theta_low=self.thresholds.theta_low,
        )

        if not subgraph.nodes:
            result = RankedResult(
                candidates=[],
                incident_id=incident_id,
                abstain_reason="no anomalous candidates",
            )
        else:
            influence = build_influence_graph(subgraph)
            isolated = isolate(
                subgraph,
                ig=influence,
                anomaly=kernel_input.anomaly,
                theta_low=self.thresholds.theta_low,
            )
            if isolated.abstain_reason:
                result = RankedResult(
                    candidates=[],
                    incident_id=incident_id,
                    abstain_reason=isolated.abstain_reason,
                )
            else:
                candidates = [
                    candidate
                    for ranked in isolated.ranked_by_cluster.values()
                    for candidate in ranked.candidates
                ]
                candidates.sort(key=lambda candidate: (-candidate.score, candidate.node_id))
                fault_class = None
                if candidates:
                    top_id = candidates[0].node_id
                    for ranked in isolated.ranked_by_cluster.values():
                        if any(c.node_id == top_id for c in ranked.candidates):
                            fault_class = ranked.fault_class
                            break
                    if fault_class is None:
                        fault_class = derive_fault_class(candidates, influence)
                result = RankedResult(
                    candidates=candidates,
                    incident_id=incident_id,
                    fault_class=fault_class,
                )

        self.store.insert_report(
            state, telemetry, anomalies, result, self.thresholds
        )
        return result

    def _load_telemetry(self, state: IncidentState) -> IncidentTelemetry:
        if state.window_start is None or state.window_end is None:
            raise ValueError(f"incident {state.incident_id} has no analysis window")
        if state.window_end <= state.window_start:
            raise ValueError(f"incident {state.incident_id} has an invalid analysis window")

        namespace = state.namespace or os.getenv("CAUSA_APP_NAMESPACE") or None
        reader = self.reader_factory()
        try:
            around = state.window_end - timedelta(minutes=1)
            lookback = int(
                (state.window_end - state.window_start - timedelta(minutes=1))
                .total_seconds()
                // 60
            )
            return reader.read_incident_window(
                incident_id=state.incident_id,
                alerted_service=state.alerted_service or "unknown",
                around=around,
                lookback_minutes=max(0, lookback),
                namespace=namespace,
                preferred_trace_id=state.trace_id,
            )
        finally:
            reader.close()
