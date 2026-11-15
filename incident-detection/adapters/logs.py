"""C1 logs boundary for classification."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from contracts import LogRecord, LogRecordEnvelope


def messages_by_node(
    records: Iterable[LogRecord] | Iterable[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Group log messages by node/service id."""
    out: dict[str, list[str]] = {}
    for rec in records:
        if isinstance(rec, LogRecord):
            nid = rec.node_id
            msg = rec.message
        elif isinstance(rec, LogRecordEnvelope):
            nid = rec.service
            msg = rec.message
        else:
            nid = str(rec.get("node_id") or rec.get("service") or "")
            msg = str(rec.get("message") or "")
        if not nid:
            continue
        out.setdefault(nid, []).append(msg)
    return out


def messages_from_fixture_logs(
    raw: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[str]]:
    """Fixture logs.json: {node_id: [{message, ...}, ...]}."""
    return {
        nid: [str(e.get("message") or "") for e in entries]
        for nid, entries in raw.items()
    }


def log_records_from_envelopes(
    envelopes: Iterable[LogRecordEnvelope],
) -> list[LogRecord]:
    """Map A's LogRecordEnvelope → narrow LogRecord for classify."""
    out: list[LogRecord] = []
    for env in envelopes:
        ts = env.timestamp or datetime.now(tz=timezone.utc)
        out.append(
            LogRecord(
                timestamp=ts,
                node_id=env.service,
                severity=env.level,
                message=env.message,
                attributes=dict(env.labels),
            )
        )
    return out
