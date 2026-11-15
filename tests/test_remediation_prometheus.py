"""Exercise freshness PromQL against an isolated local Prometheus server."""
import http.server
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time

import httpx
import pytest

from remediation.slo import snapshot_service


def test_prometheus_rejects_mixed_age_and_disappeared_contributors(monkeypatch):
    binary = shutil.which('prometheus')
    if binary is None:
        pytest.skip('local Prometheus binary unavailable')
    now = time.time()

    def samples(include_stale, *, first_sample=False):
        lines = []
        for service in ('payment','healthy'):
            sources = [('fresh', now-10, now-2)]
            if include_stale and service == 'payment':
                sources.append(('stale', now-40, now-35))
            for instance, first, last in sources:
                timestamp, value = (first,100) if first_sample else (last,200)
                labels = f'service_name="{service}",instance="{instance}"'
                stamp = int(timestamp*1000)
                lines.append(f'traces_span_metrics_calls_total{{{labels},status_code="STATUS_CODE_UNSET"}} {value} {stamp}')
                for bucket in ('100','+Inf'):
                    lines.append(f'traces_span_metrics_duration_milliseconds_bucket{{{labels},le="{bucket}"}} {value} {stamp}')
        return ('\n'.join(lines)+'\n').encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type','text/plain; version=0.0.4')
            self.end_headers()
            self.wfile.write(self.server.initial_metrics or self.server.metrics)
            self.server.initial_metrics = None

        def log_message(self, *args):
            pass

    metrics = http.server.ThreadingHTTPServer(('127.0.0.1',0), Handler)
    metrics.initial_metrics = samples(True, first_sample=True)
    metrics.metrics = samples(True)
    thread = threading.Thread(target=metrics.serve_forever,daemon=True)
    thread.start()
    proc = None
    try:
        with tempfile.TemporaryDirectory(prefix='causa-prom-') as directory:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0))
                port = sock.getsockname()[1]
            config = Path(directory)/'prometheus.yml'
            config.write_text(f'''global:
  scrape_interval: 1s
scrape_configs:
  - job_name: freshness
    honor_timestamps: true
    track_timestamps_staleness: true
    static_configs:
      - targets: ['127.0.0.1:{metrics.server_port}']
''')
            with (Path(directory)/'server.log').open('w') as log:
                proc = subprocess.Popen([binary, f'--config.file={config}', f'--storage.tsdb.path={directory}/data',
                                         f'--web.listen-address=127.0.0.1:{port}', '--log.level=error'],stdout=log,stderr=log)
                url = f'http://127.0.0.1:{port}'
                monkeypatch.setenv('PROMETHEUS_URL',url)
                deadline = time.monotonic()+10
                with httpx.Client(timeout=1) as client:
                    while True:
                        assert proc.poll() is None, (Path(directory)/'server.log').read_text()
                        try:
                            snap = snapshot_service('payment',window='60s')
                            if snap.error_rate is not None and snap.latency_p99_ms is not None:
                                break
                        except httpx.HTTPError:
                            pass
                        assert time.monotonic()<deadline, 'isolated scrape failed to produce data'
                        time.sleep(.1)
                    assert snap.success_rate == 1
                    assert snap.fresh is False
                    assert snapshot_service('healthy',window='60s').fresh is True
                    metrics.metrics = samples(False)
                    # Wait until the missing series has received its stale marker.
                    query = 'traces_span_metrics_calls_total{service_name="payment"}'
                    while True:
                        rows = client.get(url+'/api/v1/query',params={'query':query}).json()['data']['result']
                        if len(rows)==1:
                            break
                        assert time.monotonic()<deadline, 'stale marker not observed'
                        time.sleep(.1)
                    snap = snapshot_service('payment',window='60s')
                    assert snap.success_rate == 1
                    assert snap.fresh is False
            proc.terminate()
            proc.wait(timeout=5)
            proc = None
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=5)
        metrics.shutdown()
        metrics.server_close()
        thread.join(timeout=5)
