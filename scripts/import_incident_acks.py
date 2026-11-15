"""Checked, transactional import of legacy acknowledgment files; dry-run by default."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must be an ISO string with a timezone')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timestamp must include a timezone')
    return parsed


def load_records(paths):
    records = {}
    for path in paths:
        raw = json.loads(Path(path).read_text(), object_pairs_hook=_unique_pairs)
        if not isinstance(raw, dict):
            raise ValueError('acknowledgment file must contain an object')
        for key, row in raw.items():
            if not key or not isinstance(row, dict) or not row.keys() <= {'acked_at', 'acked_by', 'snoozed_until'}:
                raise ValueError('invalid acknowledgment record')
            by = row.get('acked_by')
            if not isinstance(by, str) or not by.strip():
                raise ValueError('acked_by must be a nonempty string')
            record = {'acked_at': _timestamp(row.get('acked_at')), 'acked_by': by,
                      'snoozed_until': _timestamp(row['snoozed_until']) if row.get('snoozed_until') is not None else None}
            if key in records and records[key] != record:
                raise ValueError(f'conflicting acknowledgment records: {key}')
            records[key] = record
    return records


def import_records(conn, records, *, apply=False):
    inserted = 0
    with conn.transaction():
        for key, row in records.items():
            expected = (row['acked_at'], row['acked_by'], row['snoozed_until'])
            if apply:
                new = conn.execute(
                    '''INSERT INTO dashboard_incident_state (incident_key, acked_at, acked_by, snoozed_until)
                       VALUES (%s, %s, %s, %s) ON CONFLICT (incident_key) DO NOTHING RETURNING incident_key''',
                    (key, *expected),
                ).fetchone()
                inserted += int(new is not None)
            existing = conn.execute(
                'SELECT acked_at, acked_by, snoozed_until FROM dashboard_incident_state WHERE incident_key=%s FOR UPDATE',
                (key,),
            ).fetchone()
            if existing is not None and tuple(existing) != expected:
                raise ValueError(f'conflicting database acknowledgment: {key}')
            if apply and existing is None:
                raise RuntimeError('import verification failed')
    return {'validated': len(records), 'inserted': inserted, 'mode': 'apply' if apply else 'dry-run'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', type=Path)
    parser.add_argument('--apply', action='store_true', help='commit verified rows; never overwrite existing state')
    args = parser.parse_args()
    from dashboard.backend.db import dsn_from_env
    import psycopg
    dsn = dsn_from_env()
    if not dsn:
        parser.error('configure CAUSA_PG_DSN or PGHOST before importing')
    try:
        records = load_records(args.files)
        with psycopg.connect(dsn, autocommit=True) as conn:
            print(json.dumps(import_records(conn, records, apply=args.apply)))
    except (OSError, ValueError, RuntimeError, psycopg.Error) as exc:
        parser.exit(1, f'Import failed; no changes committed ({type(exc).__name__}).\n')


if __name__ == '__main__':
    main()
