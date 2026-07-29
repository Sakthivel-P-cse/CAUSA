from __future__ import annotations

from types import SimpleNamespace

from remediation.worker import _dispatch_rollback


def test_irreversible_actions_never_report_fake_rollback_success():
    for action in (
        "rollback_to_last_good",
        "scale_restart_cordon",
        "restart_data_path",
    ):
        result = _dispatch_rollback({}, SimpleNamespace(action=action))
        assert result["attempted"] is False
        assert result["status"] == "failed"
