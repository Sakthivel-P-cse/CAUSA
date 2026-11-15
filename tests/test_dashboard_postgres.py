"""Persistence check against a disposable Unix-socket PostgreSQL cluster."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

import pytest


def test_operator_state_survives_processes_and_honors_database_errors(monkeypatch):
    if not all(shutil.which(command) for command in ('initdb', 'pg_ctl')):
        pytest.skip('local PostgreSQL binaries unavailable')
    if os.geteuid() == 0:
        pytest.skip('PostgreSQL initdb requires an unprivileged user')
    import psycopg
    from psycopg.conninfo import make_conninfo
    from dashboard.backend import ack_store
    from dashboard.backend.db import DashboardStore

    def run(*args):
        return subprocess.run(args, check=True, capture_output=True, text=True)

    with tempfile.TemporaryDirectory(prefix='causa-pg-') as directory:
        data = str(Path(directory) / 'data')
        run('initdb', '-D', data, '-A', 'trust', '--no-locale', '--encoding=UTF8')
        run('pg_ctl', '-D', data, '-l', str(Path(directory)/'server.log'), '-o', f"-k {directory} -h ''", '-w', 'start')
        try:
            admin = make_conninfo(host=directory, dbname='postgres')
            root = Path(__file__).resolve().parents[1]
            statements = []
            for name in ('configmap.yaml', 'schema-migrate-job.yaml'):
                text = (root/'incident-detection/helm/postgres/templates'/name).read_text()
                ddl = re.search(r'CREATE TABLE IF NOT EXISTS dashboard_incident_state \(.*?\);', text, re.S).group()
                statements.append(' '.join(ddl.split()))
            assert statements[0] == statements[1]
            with psycopg.connect(admin, autocommit=True) as conn:
                conn.execute(statements[0])
                conn.execute(statements[1])  # upgrade is repeatable
                conn.execute('CREATE TABLE remediation_plans (id uuid PRIMARY KEY)')
                conn.execute('CREATE TABLE remediation_events (id uuid PRIMARY KEY, plan_id uuid, event_type text, payload jsonb, created_at timestamptz)')
                conn.execute('CREATE ROLE dashboard_test LOGIN')
                conn.execute('GRANT SELECT, INSERT, UPDATE ON dashboard_incident_state TO dashboard_test')
                conn.execute('CREATE TABLE rca_reports (id uuid PRIMARY KEY, incident_id text, feedback_verdict text, feedback_notes text, feedback_updated_at timestamptz)')
                conn.execute("INSERT INTO rca_reports(id,incident_id) VALUES ('00000000-0000-0000-0000-000000000001','incident')")
                conn.execute('GRANT SELECT, UPDATE ON rca_reports TO dashboard_test')
                conn.execute('GRANT SELECT, INSERT ON remediation_events TO dashboard_test')
            dsn = make_conninfo(host=directory, dbname='postgres', user='dashboard_test')
            monkeypatch.setenv('CAUSA_PG_DSN', dsn)
            first = ack_store.ack_incident('incident', by='first')
            snoozed = ack_store.snooze_incident('incident', minutes=5, by='second')
            assert snoozed['acked_at'] == first['acked_at']
            assert snoozed['snoozed_until'] is not None
            code = 'import json; from dashboard.backend.ack_store import get_ack; print(json.dumps(get_ack("incident")))'
            child = json.loads(run(sys.executable, '-c', code).stdout)
            assert child == snoozed
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda n: ack_store.ack_incident('concurrent', by=str(n)), range(8)))
            with psycopg.connect(admin, autocommit=True) as conn:
                assert conn.execute("SELECT count(*) FROM dashboard_incident_state WHERE incident_key='concurrent'").fetchone()[0] == 1
            assert ack_store.ack_incident('incident')['snoozed_until'] is None
            from dashboard.backend.remediation_store import RemediationStore
            from remediation.slo import SLOSnapshot, evaluate_gates
            monkeypatch.setenv('CAUSA_REMEDIATION_PG_DSN', dsn)
            snap = SLOSnapshot('payment', float('nan'), float('inf'), 1, True, 'now')
            rem = RemediationStore()
            rem.add_event('00000000-0000-0000-0000-000000000001', 'slo_pre', snap.to_dict())
            outcome = evaluate_gates(snap, error_rate_max=.05, latency_p99_ms_max=800, success_rate_min=.95)
            rem.add_event('00000000-0000-0000-0000-000000000001', 'observe', outcome)
            with psycopg.connect(admin, autocommit=True) as conn:
                payload = conn.execute("SELECT payload FROM remediation_events WHERE event_type='observe'").fetchone()[0]
                assert payload['verified'] is False
                assert payload['snapshot']['error_rate'] is None
            from datetime import datetime, timezone
            from scripts.import_incident_acks import import_records
            legacy = {'acked_at': datetime(2026, 1, 1, tzinfo=timezone.utc), 'acked_by': 'legacy', 'snoozed_until': None}
            with psycopg.connect(dsn, autocommit=True) as conn:
                assert import_records(conn, {'legacy': legacy})['inserted'] == 0
                assert ack_store.get_ack('legacy') is None
                assert import_records(conn, {'legacy': legacy}, apply=True)['inserted'] == 1
                assert import_records(conn, {'legacy': legacy}, apply=True)['inserted'] == 0
                with pytest.raises(ValueError, match='conflicting'):
                    import_records(conn, {'new': legacy, 'legacy': {**legacy, 'acked_by': 'conflict'}}, apply=True)
                assert ack_store.get_ack('new') is None
                assert ack_store.get_ack('legacy')['acked_by'] == 'legacy'
            store = DashboardStore()
            assert store.update_feedback('absent', verdict='accepted', notes=None) is None
            updated = store.update_feedback('incident', verdict='accepted', notes='persisted')
            assert updated['feedback']['notes'] == 'persisted'
            with psycopg.connect(admin, autocommit=True) as conn:
                assert conn.execute("SELECT feedback_notes FROM rca_reports WHERE incident_id='incident'").fetchone()[0] == 'persisted'
                conn.execute('REVOKE UPDATE ON rca_reports FROM dashboard_test')
                conn.execute('REVOKE SELECT, INSERT, UPDATE ON dashboard_incident_state FROM dashboard_test')
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                store.update_feedback('incident', verdict='rejected', notes=None)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                ack_store.ack_incident('incident')
        finally:
            run('pg_ctl', '-D', data, '-m', 'immediate', '-w', 'stop')
