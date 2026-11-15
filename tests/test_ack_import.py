import json
from datetime import datetime, timezone

import pytest

from scripts.import_incident_acks import load_records


def test_ack_import_validates_and_merges_identical_records(tmp_path):
    record = {'acked_at': '2026-01-01T00:00:00Z', 'acked_by': 'operator', 'snoozed_until': None}
    path = tmp_path/'acks.json'
    path.write_text(json.dumps({'incident': record}))
    rows = load_records([path, path])
    assert len(rows) == 1
    assert rows['incident']['acked_at'] == datetime(2026, 1, 1, tzinfo=timezone.utc)
    conflict = tmp_path/'conflict.json'
    conflict.write_text(json.dumps({'incident': {**record, 'acked_by': 'other'}}))
    with pytest.raises(ValueError, match='conflicting'):
        load_records([path, conflict])


@pytest.mark.parametrize('value', [[], {'id': None}, {'id': {'acked_by':'x','acked_at':'not-a-date'}}, {'id': {'acked_by':'x','acked_at':'2026-01-01T00:00:00'}}])
def test_ack_import_rejects_malformed_sources(tmp_path, value):
    path = tmp_path/'acks.json'; path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load_records([path])
