"""C1 logs boundary for classification."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from contracts import LogRecord


def messages_by_node(
    records: Iterable[LogRecord] | Iterable[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Group log messages by node/service id."""
    out: dict[str, list[str]] = {}
    for rec in records:
        if isinstance(rec, LogRecord):
            nid = rec.node_id
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
