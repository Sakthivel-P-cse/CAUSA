"""Regression tests for fail-closed production safety controls."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard.backend.auth import (
    authenticate,
    configured_tokens,
    validate_auth_configuration,
)
from dashboard.backend.remediation_store import RemediationStore
from remediation.auto import auto_remediate_from_report
from remediation.calibration_gate import automation_calibrated
from remediation.policy import check_approve_identity


def _clear_pg(monkeypatch) -> None:
    for name in (
        "CAUSA_PG_DSN",
        "PGHOST",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGPASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_api_auth_is_required_and_role_is_derived(monkeypatch):
    monkeypatch.setenv("CAUSA_API_REQUIRE_AUTH", "1")
    monkeypatch.setenv("CAUSA_API_VIEWER_TOKEN", "viewer-secret")
    monkeypatch.setenv("REMEDIATION_APPROVAL_TOKEN", "approver-secret")
    assert configured_tokens()["viewer-secret"] == "viewer"
    assert authenticate("Bearer approver-secret").role == "approver"
    with pytest.raises(Exception):
        authenticate(None)
    with pytest.raises(Exception):
        authenticate("Bearer wrong")


def test_approval_fails_closed_without_token_configuration(monkeypatch):
    monkeypatch.setenv("CAUSA_API_REQUIRE_AUTH", "1")
    monkeypatch.setenv("CAUSA_API_VIEWER_TOKEN", "viewer-secret")
    monkeypatch.delenv("REMEDIATION_APPROVAL_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="REMEDIATION_APPROVAL_TOKEN"):
        validate_auth_configuration()
    decision = check_approve_identity(None)
    assert decision.allowed is False
    assert decision.code == "auth_misconfigured"


def test_automation_defaults_off(monkeypatch):
    monkeypatch.delenv("CAUSA_REMEDIATION_AUTO_PROPOSE", raising=False)
    monkeypatch.delenv("CAUSA_REMEDIATION_AUTO_APPLY", raising=False)
    result = auto_remediate_from_report({"incident_id": "inc-safe-default"})
    assert result == {"skipped": True, "reason": "auto_propose_disabled"}


def test_remediation_store_requires_postgres_by_default(monkeypatch):
    _clear_pg(monkeypatch)
    monkeypatch.delenv("CAUSA_REMEDIATION_ALLOW_FILE_STORE", raising=False)
    with pytest.raises(RuntimeError, match="Postgres is required"):
        RemediationStore()


def test_development_file_store_enforces_worker_ownership(monkeypatch, tmp_path):
    _clear_pg(monkeypatch)
    monkeypatch.setenv("CAUSA_REMEDIATION_ALLOW_FILE_STORE", "1")
    monkeypatch.setenv("CAUSA_REMEDIATION_STORE", str(tmp_path / "plans.json"))
    store = RemediationStore()
    plan = store.create_proposed(
        {
            "incident_id": "inc-lease",
            "candidate_node": "payment",
            "fault_class": "Resource",
            "action": "scale_up",
            "depth": "deep",
        }
    )
    store.set_status(plan["id"], "approved", decided_by="test")
    claimed = store.claim_next("worker-a", lease_seconds=60)
    assert claimed and claimed["claimed_by"] == "worker-a"
    with pytest.raises(RuntimeError, match="no longer owns"):
        store.set_status_if_owned(plan["id"], "executing", "worker-b")
    assert store.set_status_if_owned(plan["id"], "executing", "worker-a")["status"] == "executing"

    expired = store.get(plan["id"])
    expired["lease_until"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    store._update(expired)
    with pytest.raises(RuntimeError, match="no longer owns"):
        store.set_status_if_owned(plan["id"], "succeeded", "worker-a")
    reclaimed = store.claim_next("worker-b", lease_seconds=60)
    assert reclaimed and reclaimed["claimed_by"] == "worker-b"


def test_calibration_gate_requires_real_sample_count(tmp_path):
    artifact = tmp_path / "fit.json"
    artifact.write_text(
        json.dumps(
            {
                "release_ready": True,
                "split_version": "rca-stratified-v1",
                "dataset_sha256": "a" * 64,
                "n": 30,
                "calibration": {
                    "n": 30,
                    "mapping": [[0.0, 0.1], [1.0, 0.9]],
                },
                "holdout": {"ranking": {"n": 10}},
            }
        ),
        encoding="utf-8",
    )
    assert automation_calibrated(artifact)
    raw = json.loads(artifact.read_text(encoding="utf-8"))
    raw["calibration"]["n"] = 3
    artifact.write_text(json.dumps(raw), encoding="utf-8")
    assert not automation_calibrated(artifact)
