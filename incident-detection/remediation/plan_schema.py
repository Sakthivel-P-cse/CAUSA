"""Typed immutable remediation plan specifications."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime import profile as runtime_profile


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ExactTarget:
    cluster: str
    namespace: str
    kind: str
    name: str
    config_key: str | None = None
    revision: str | None = None
    container: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True)
class PlanChange:
    description: str
    patch: dict[str, Any]
    inverse: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SLOGate:
    error_rate_max: float = 0.05
    latency_p99_ms_max: float = 800.0
    success_rate_min: float = 0.95
    window_seconds: int = 60
    require_fresh: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TypedPlanSpec:
    action: str
    fault_class: str
    target: ExactTarget
    before_fingerprint: str
    change: PlanChange
    risk_tier: str
    required_approvals: int
    slo_gates: SLOGate
    incident_id: str
    candidate_node: str
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 600
    expires_at: str = ""
    idempotency_key: str = ""
    schema_version: str = "1"

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            self.idempotency_key = self._compute_idempotency_key()
        if not self.expires_at:
            self.expires_at = (_utcnow() + timedelta(hours=2)).isoformat()

    def _compute_idempotency_key(self) -> str:
        payload = {
            "action": self.action,
            "target": self.target.to_dict(),
            "before": self.before_fingerprint,
            "change": self.change.patch,
            "incident": self.incident_id,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        return f"rem-{digest[:24]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "action": self.action,
            "fault_class": self.fault_class,
            "target": self.target.to_dict(),
            "before_fingerprint": self.before_fingerprint,
            "change": self.change.to_dict(),
            "risk_tier": self.risk_tier,
            "required_approvals": self.required_approvals,
            "slo_gates": self.slo_gates.to_dict(),
            "incident_id": self.incident_id,
            "candidate_node": self.candidate_node,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "timeout_seconds": self.timeout_seconds,
            "expires_at": self.expires_at,
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TypedPlanSpec:
        target_raw = raw.get("target") or {}
        change_raw = raw.get("change") or {}
        gates_raw = raw.get("slo_gates") or {}
        target = ExactTarget(
            cluster=str(target_raw.get("cluster") or "kind-gcp-demo-test"),
            namespace=str(target_raw.get("namespace") or runtime_profile.remediation_namespace()),
            kind=str(target_raw.get("kind") or "ConfigMap"),
            name=str(target_raw.get("name") or ""),
            config_key=target_raw.get("config_key"),
            revision=target_raw.get("revision"),
            container=target_raw.get("container"),
        )
        if not target.name:
            raise ValueError("plan target.name is required")
        change = PlanChange(
            description=str(change_raw.get("description") or ""),
            patch=dict(change_raw.get("patch") or {}),
            inverse=dict(change_raw.get("inverse") or {}),
        )
        if not change.patch:
            raise ValueError("plan change.patch is required")
        if not change.inverse:
            raise ValueError("plan change.inverse is required")
        gates = SLOGate(
            error_rate_max=float(gates_raw.get("error_rate_max", 0.05)),
            latency_p99_ms_max=float(gates_raw.get("latency_p99_ms_max", 800.0)),
            success_rate_min=float(gates_raw.get("success_rate_min", 0.95)),
            window_seconds=int(gates_raw.get("window_seconds", 60)),
            require_fresh=bool(gates_raw.get("require_fresh", True)),
        )
        return cls(
            action=str(raw.get("action") or ""),
            fault_class=str(raw.get("fault_class") or ""),
            target=target,
            before_fingerprint=str(raw.get("before_fingerprint") or ""),
            change=change,
            risk_tier=str(raw.get("risk_tier") or "medium"),
            required_approvals=int(raw.get("required_approvals") or 1),
            slo_gates=gates,
            incident_id=str(raw.get("incident_id") or ""),
            candidate_node=str(raw.get("candidate_node") or target.name),
            confidence=float(raw.get("confidence") or 0.0),
            evidence=dict(raw.get("evidence") or {}),
            timeout_seconds=int(raw.get("timeout_seconds") or 600),
            expires_at=str(raw.get("expires_at") or ""),
            idempotency_key=str(raw.get("idempotency_key") or ""),
            schema_version=str(raw.get("schema_version") or "1"),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        return (now or _utcnow()) > exp


def new_plan_id() -> str:
    return str(uuid.uuid4())


def fingerprint_payload(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()
