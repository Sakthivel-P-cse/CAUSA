"""Execution adapter registry for remediation actions."""

from __future__ import annotations

from typing import Any, Callable

ExecuteFn = Callable[[dict[str, Any]], dict[str, Any]]
RollbackFn = Callable[[dict[str, Any]], dict[str, Any]]


def execute_adapter(action: str) -> ExecuteFn | None:
    if action == "config_patch":
        from remediation.apply.k8s import apply_config_patch

        return apply_config_patch
    if action == "rollback_to_last_good":
        from remediation.apply.k8s import apply_dependency_rollback

        return apply_dependency_rollback
    if action == "scale_up":
        from remediation.apply.k8s import apply_resource_patch

        return apply_resource_patch
    if action == "code_fix_pr":
        from remediation.apply.github_pr import open_draft_pr

        return open_draft_pr
    if action in ("scale_restart_cordon", "restart_data_path"):
        from remediation.apply.k8s import apply_restart_workload

        return apply_restart_workload
    return None


def rollback_adapter(action: str) -> RollbackFn | None:
    if action == "config_patch":
        from remediation.apply.k8s import rollback_config_patch

        return rollback_config_patch
    if action == "scale_up":
        from remediation.apply.k8s import apply_resource_patch

        return lambda row: apply_resource_patch(row, use_inverse=True)
    return None

