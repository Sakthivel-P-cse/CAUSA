from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_PATH = Path(__file__).resolve().parents[2] / ".run" / "incident_acks.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    if not _PATH.exists():
        return {}
    try:
        raw = json.loads(_PATH.read_text())
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save(data: dict[str, Any]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(data, indent=2))


def get_ack(incident_key: str) -> dict[str, Any] | None:
    with _LOCK:
        row = _load().get(str(incident_key))
        return dict(row) if isinstance(row, dict) else None


def ack_incident(incident_key: str, *, by: str = "operator") -> dict[str, Any]:
    key = str(incident_key)
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
    with _LOCK:
        data = _load()
        prev = data.get(key) if isinstance(data.get(key), dict) else {}
        row = {
            **prev,
            "acked_at": prev.get("acked_at") or _now(),
            "acked_by": by,
            "snoozed_until": datetime.fromtimestamp(until, tz=timezone.utc).isoformat(),
        }
        data[key] = row
        _save(data)
        return dict(row)


def merge_ack_fields(item: dict[str, Any]) -> dict[str, Any]:
    key = str(item.get("incident_id") or item.get("id") or "")
    ack = get_ack(key) if key else None
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
