"""Eval harness — Dev C headline metrics, baselines, tuning, calibration."""

# Keep __init__ light so `python -m eval.runner` does not double-import.
__all__ = [
    "EvalMetrics",
    "EvalRunner",
    "compute_metrics",
    "most_anomalous_rank",
    "random_rank",
    "run_golden_fixture",
    "subgraph_recall",
]


def __getattr__(name: str):
    if name in ("EvalMetrics", "compute_metrics", "subgraph_recall"):
        from eval.metrics import EvalMetrics, compute_metrics, subgraph_recall

        return {
            "EvalMetrics": EvalMetrics,
            "compute_metrics": compute_metrics,
            "subgraph_recall": subgraph_recall,
        }[name]
    if name in ("most_anomalous_rank", "random_rank"):
        from eval.baselines import most_anomalous_rank, random_rank

        return {"most_anomalous_rank": most_anomalous_rank, "random_rank": random_rank}[name]
    if name in ("EvalRunner", "run_golden_fixture"):
        from eval.runner import EvalRunner, run_golden_fixture

        return {"EvalRunner": EvalRunner, "run_golden_fixture": run_golden_fixture}[name]
    raise AttributeError(name)
