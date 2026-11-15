import json
import unittest

import pytest
from unittest.mock import MagicMock, patch

from dashboard.backend.db import DashboardStore
from dashboard.backend import ack_store


class DashboardImprovements(unittest.TestCase):
    def test_joined_status_and_legacy_fallback(self):
        store = DashboardStore()
        self.assertEqual(store._normalize_report({'status': 'resolved'}, None)['incident_status'], 'resolved')
        self.assertEqual(store._normalize_report({'status': 'resolved'}, {'status': 'open'})['incident_status'], 'open')
        self.assertEqual(store._normalize_report({'resolved_at': '2026-01-01'}, None)['incident_status'], 'resolved')

    def test_feedback_failures_are_not_success(self):
        store = DashboardStore()
        store._use_pg = True
        store._connect = MagicMock(side_effect=RuntimeError('denied'))
        with self.assertRaises(RuntimeError):
            store.update_feedback('missing', verdict='accepted', notes=None)
        store._use_pg = False
        with self.assertRaises(RuntimeError):
            store.update_feedback('missing', verdict='accepted', notes=None)

    def test_ack_uses_configured_database(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = ('2026-01-01', 'operator', None)
        with patch('dashboard.backend.db.dsn_from_env', return_value='configured'), patch.object(DashboardStore, '_connect', return_value=conn):
            row = ack_store.ack_incident('incident-1')
        self.assertEqual(row['acked_by'], 'operator')
        self.assertTrue(conn.execute.called)

    def test_feedback_missing_and_success(self):
        store = DashboardStore()
        store._use_pg = True
        conn = MagicMock()
        conn.__enter__.return_value = conn
        store._connect = MagicMock(return_value=conn)
        conn.execute.return_value.fetchone.return_value = None
        self.assertIsNone(store.update_feedback('missing', verdict='accepted', notes=None))
        conn.execute.return_value.fetchone.return_value = ('now',)
        self.assertEqual(store.update_feedback('id', verdict='accepted', notes='note')['feedback']['notes'], 'note')

    def test_ack_database_failure_is_visible(self):
        with patch('dashboard.backend.db.dsn_from_env', return_value='configured'), patch.object(DashboardStore, '_connect', side_effect=RuntimeError('denied')):
            with self.assertRaises(RuntimeError):
                ack_store.ack_incident('id')
            with self.assertRaises(RuntimeError):
                ack_store.get_acks()

    def test_local_ack_and_snooze(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory, patch.object(ack_store, '_PATH', Path(directory) / 'acks.json'), patch('dashboard.backend.db.dsn_from_env', return_value=None):
            snoozed = ack_store.snooze_incident('id', minutes=5)
            self.assertIsNotNone(snoozed['snoozed_until'])
            self.assertEqual(ack_store.get_ack('id'), snoozed)
            self.assertIsNone(ack_store.ack_incident('id')['snoozed_until'])

    def test_stats_use_nested_status_counts(self):
        from dashboard.backend.routers.stats import get_stats
        store = MagicMock()
        store.list_incidents_joined.return_value = []
        with patch('dashboard.backend.routers.stats.get_acks', return_value={}), patch('dashboard.backend.routers.stats.RemediationStore') as rem:
            rem.return_value.stats.return_value = {'by_status': {'succeeded': 2, 'failed': 1, 'rolled_back': 1}}
            result = get_stats(service=None, minutes=60, start=None, end=None, store=store)
            self.assertEqual(result['remediation_success_rate'], .5)
            self.assertIsNone(result['auto_remediation_rate'])
            rem.return_value.stats.return_value = {'by_status': {}}
            self.assertIsNone(get_stats(service=None, minutes=60, start=None, end=None, store=store)['remediation_success_rate'])


if __name__ == '__main__':
    unittest.main()


def test_feedback_database_failure_maps_to_unavailable():
    from fastapi import HTTPException
    from dashboard.backend.routers.incidents import post_feedback
    store = MagicMock()
    store.update_feedback.side_effect = RuntimeError('private database detail')
    with pytest.raises(HTTPException) as error:
        post_feedback('id', {'verdict': 'accepted'}, store)
    assert error.value.status_code == 503
    assert 'private' not in str(error.value.detail)


def test_local_ack_failed_replace_preserves_previous_state(tmp_path):
    path = tmp_path / 'acks.json'
    path.write_text('{"existing": {"acked_by": "operator"}}')
    with patch.object(ack_store, '_PATH', path), patch('pathlib.Path.replace', side_effect=OSError('disk failure')):
        with pytest.raises(OSError):
            ack_store._save({'new': {}})
    assert 'existing' in json.loads(path.read_text())
