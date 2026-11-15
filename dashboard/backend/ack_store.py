from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# shortcut: file fallback is single-process; use Postgres for replicas.
_LOCK = threading.Lock()
_PATH = Path(__file__).resolve().parents[2] / ".run" / "incident_acks.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    if not _PATH.exists():
        return {}
    try:
        raw = json.loads(_PATH.read_text())
        if not isinstance(raw, dict):
            raise ValueError("expected an object")
        return raw
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read acknowledgment file: {exc}") from exc


def _save(data: dict[str, Any]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2))
    temporary.replace(_PATH)


def get_acks() -> dict[str, dict[str, Any]]:
    from dashboard.backend.db import DashboardStore, _iso
    store = DashboardStore()
    if store._use_pg:
        with store._connect() as conn:
            rows = conn.execute("SELECT incident_key, acked_at, acked_by, snoozed_until FROM dashboard_incident_state").fetchall()
        return {str(key): {"acked_at": _iso(at), "acked_by": by, "snoozed_until": _iso(until)} for key, at, by, until in rows}
    with _LOCK:
        return _load()


def get_ack(incident_key: str) -> dict[str, Any] | None:
    return get_acks().get(str(incident_key))


def _pg_write(key: str, by: str, until: str | None, *, snooze: bool) -> dict[str, Any] | None:
    from dashboard.backend.db import DashboardStore, _iso
    store = DashboardStore()
    if not store._use_pg:
        return None
    with store._connect() as conn:
        row = conn.execute(
            """INSERT INTO dashboard_incident_state (incident_key, acked_at, acked_by, snoozed_until)
               VALUES (%s, NOW(), %s, %s)
               ON CONFLICT (incident_key) DO UPDATE SET
                 acked_at = CASE WHEN %s THEN dashboard_incident_state.acked_at ELSE NOW() END,
                 acked_by = EXCLUDED.acked_by, snoozed_until = EXCLUDED.snoozed_until
               RETURNING acked_at, acked_by, snoozed_until""",
            (key, by, until, snooze),
        ).fetchone()
    return {"acked_at": _iso(row[0]), "acked_by": row[1], "snoozed_until": _iso(row[2])}


def ack_incident(incident_key: str, *, by: str = "operator") -> dict[str, Any]:
    key = str(incident_key)
    persisted = _pg_write(key, by, None, snooze=False)
    if persisted is not None:
        return persisted
    with _LOCK:
        data = _load()
        row = {
            "acked_at": _now(),
            "acked_by": by,
            "snoozed_until": None,
        }
        data[key] = row
        _save(data)
        return dict(row)


def snooze_incident(incident_key: str, *, minutes: int = 60, by: str = "operator") -> dict[str, Any]:
    key = str(incident_key)
    until = datetime.now(timezone.utc).timestamp() + max(1, minutes) * 60
    until_iso = datetime.fromtimestamp(until, tz=timezone.utc).isoformat()
    persisted = _pg_write(key, by, until_iso, snooze=True)
    if persisted is not None:
        return persisted
    with _LOCK:
        data = _load()
        prev = data.get(key) if isinstance(data.get(key), dict) else {}
        row = {
            **prev,
            "acked_at": prev.get("acked_at") or _now(),
            "acked_by": by,
            "snoozed_until": until_iso,
        }
        data[key] = row
        _save(data)
        return dict(row)


def merge_ack_fields(item: dict[str, Any], acks: dict[str, Any] | None = None) -> dict[str, Any]:
    key = str(item.get("incident_id") or item.get("id") or "")
    ack = (acks.get(key) if acks is not None else get_ack(key)) if key else None
    out = dict(item)
    if ack:
        out["acked_at"] = ack.get("acked_at")
        out["acked_by"] = ack.get("acked_by")
        out["snoozed_until"] = ack.get("snoozed_until")
    return out


def lifecycle_for(item: dict[str, Any]) -> str:
    if item.get("resolved_at") or str(item.get("incident_status") or "").lower() == "resolved":
        return "resolved"
    if item.get("acked_at"):
        return "investigating"
    return "active"
