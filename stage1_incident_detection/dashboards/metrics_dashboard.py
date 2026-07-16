#!/usr/bin/env python3
"""
metrics_dashboard.py
====================
Serves a live Metrics dashboard on http://localhost:5002

Queries Prometheus's HTTP API for key OTel Collector + service metrics
and renders a card-grid dashboard that auto-refreshes every 10 seconds.

Usage:
    python3 metrics_dashboard.py

Prerequisites:
    kubectl -n prometheus port-forward svc/prometheus 9090:9090
"""

import json
import urllib.request
import urllib.parse
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

PROMETHEUS_URL = "http://localhost:9090"
DASHBOARD_PORT = 5002
REFRESH_SECONDS = 10

# Queries to show — (display label, PromQL, unit)
QUERIES = [
    ("Targets Up",            "sum(up)",                                          ""),
    ("OTel Spans Received/s", "rate(otelcol_receiver_accepted_spans_total[1m])",  "spans/s"),
    ("OTel Metrics Recv/s",   "rate(otelcol_receiver_accepted_metric_points_total[1m])", "pts/s"),
    ("OTel Log Records/s",    "rate(otelcol_receiver_accepted_log_records_total[1m])",    "logs/s"),
    ("Spans Exported/s",      "rate(otelcol_exporter_sent_spans_total[1m])",      "spans/s"),
    ("Metrics Exported/s",    "rate(otelcol_exporter_sent_metric_points_total[1m])","pts/s"),
    ("Logs Exported/s",       "rate(otelcol_exporter_sent_log_records_total[1m])","logs/s"),
    ("Queue Size",            "otelcol_exporter_queue_size",                      "items"),
    ("CPU (system)",          "rate(process_cpu_seconds_total[1m])",              "cores"),
    ("Memory (RSS)",          "process_resident_memory_bytes / 1048576",          "MiB"),
    ("Batch Size Avg",        "otelcol_processor_batch_batch_size_trigger_send",  ""),
    ("Dropped Spans",         "rate(otelcol_exporter_enqueue_failed_spans_total[1m])", "spans/s"),
]

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>TraceOps · Metrics Dashboard</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/>
  <style>
    :root {{
      --bg: #0d1117;
      --surface: #161b22;
      --surface2: #21262d;
      --border: #30363d;
      --accent: #58a6ff;
      --green: #3fb950;
      --yellow: #d29922;
      --red: #f85149;
      --orange: #f0883e;
      --purple: #bc8cff;
      --text: #e6edf3;
      --muted: #8b949e;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }}
    header {{
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 16px 32px;
      display: flex; align-items: center; justify-content: space-between;
      position: sticky; top: 0; z-index: 10;
    }}
    header h1 {{ font-size: 1.3rem; font-weight: 700; display: flex; align-items: center; gap: 10px; }}
    .badge {{
      background: linear-gradient(135deg, #1f6feb, #388bfd);
      color: #fff; font-size: 0.7rem; font-weight: 600;
      padding: 3px 10px; border-radius: 20px; letter-spacing: 0.05em;
    }}
    .meta {{ font-size: 0.8rem; color: var(--muted); }}
    .meta span {{ color: var(--accent); }}
    main {{ max-width: 1400px; margin: 0 auto; padding: 28px 32px; }}
    .section-header {{
      display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px;
    }}
    .section-header h2 {{ font-size: 1rem; font-weight: 600; }}
    .refresh-badge {{
      font-size: 0.72rem; color: var(--muted);
      background: var(--surface2); border: 1px solid var(--border);
      padding: 3px 10px; border-radius: 20px;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      gap: 18px;
      margin-bottom: 36px;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 22px 20px;
      transition: border-color 0.2s, transform 0.15s;
      position: relative;
      overflow: hidden;
    }}
    .card::before {{
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0; height: 3px;
      background: var(--card-color, var(--accent));
      opacity: 0.7;
    }}
    .card:hover {{ border-color: var(--accent); transform: translateY(-2px); }}
    .card .label {{
      font-size: 0.72rem; color: var(--muted);
      text-transform: uppercase; letter-spacing: 0.08em;
      margin-bottom: 12px;
    }}
    .card .value {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 2rem; font-weight: 600;
      color: var(--card-color, var(--accent));
      line-height: 1;
    }}
    .card .unit {{
      font-size: 0.75rem; color: var(--muted);
      margin-top: 6px;
    }}
    .card .query-hint {{
      font-size: 0.65rem; color: var(--border);
      margin-top: 10px; font-family: 'JetBrains Mono', monospace;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }}
    .card:hover .query-hint {{ color: var(--muted); }}
    .card.no-data .value {{ color: var(--muted); }}
    .raw-table-wrap {{
      background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow: hidden;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
    thead tr {{ background: var(--surface2); border-bottom: 1px solid var(--border); }}
    th {{
      padding: 10px 16px; text-align: left;
      font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em;
      color: var(--muted); font-weight: 600;
    }}
    tbody tr {{ border-bottom: 1px solid var(--border); transition: background 0.15s; }}
    tbody tr:last-child {{ border-bottom: none; }}
    tbody tr:hover {{ background: var(--surface2); }}
    td {{ padding: 9px 16px; font-family: 'JetBrains Mono', monospace; font-size: 0.78rem; }}
    .td-metric {{ color: var(--accent); }}
    .td-label {{ color: var(--muted); word-break: break-all; }}
    .td-value {{ color: var(--green); text-align: right; }}
    .pulse {{
      display: inline-block; width: 8px; height: 8px;
      border-radius: 50%; background: var(--accent);
      animation: pulse 2s infinite; margin-right: 8px;
    }}
    @keyframes pulse {{ 0%,100% {{ opacity:1; transform:scale(1);}} 50% {{ opacity:0.5; transform:scale(1.3);}} }}
    footer {{
      text-align: center; padding: 24px; font-size: 0.75rem;
      color: var(--muted); border-top: 1px solid var(--border); margin-top: 40px;
    }}
    #countdown {{ font-weight: 600; color: var(--accent); }}
    .error-box {{ text-align: center; padding: 48px 0; color: var(--muted); }}
    .error-box .icon {{ font-size: 2.5rem; margin-bottom: 12px; }}
    .error-box p {{ font-size: 0.85rem; }}
    .error-box code {{
      background: var(--surface2); padding: 2px 8px;
      border-radius: 4px; color: var(--accent);
    }}
  </style>
</head>
<body>
<header>
  <h1>
    <span class="pulse"></span>
    TraceOps <span style="color:var(--muted);font-weight:300;margin:0 4px;">·</span> Metrics Dashboard
    <span class="badge">LIVE</span>
  </h1>
  <div class="meta">Source: <span>Prometheus :9090</span> &nbsp;|&nbsp; Refreshes in <span id="countdown">{refresh}s</span></div>
</header>
<main>
  {body}
</main>
<footer>
  TraceOps · Stage 1 Incident Detection &nbsp;|&nbsp; Metrics Dashboard · Port {port}
</footer>
<script>
  let t = {refresh};
  const el = document.getElementById('countdown');
  const _timer = setInterval(() => {{
    t--;
    if (t <= 0) {{ clearInterval(_timer); location.reload(); return; }}
    el.textContent = t + 's';
  }}, 1000);
</script>
</body>
</html>"""

CARD_COLORS = [
    "#58a6ff", "#3fb950", "#d29922", "#f0883e",
    "#bc8cff", "#f85149", "#39d353", "#79c0ff",
    "#f0883e", "#e3b341", "#58a6ff", "#f85149",
]


def prom_query(expr: str):
    params = urllib.parse.urlencode({"query": expr})
    url = f"{PROMETHEUS_URL}/api/v1/query?{params}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            results = data.get("data", {}).get("result", [])
            return results, None
    except Exception as exc:
        return None, str(exc)


def fmt_value(val: str) -> str:
    try:
        f = float(val)
        if f == 0:
            return "0"
        if f >= 1_000_000:
            return f"{f/1_000_000:.2f}M"
        if f >= 1_000:
            return f"{f/1_000:.2f}K"
        if f < 0.001:
            return f"{f:.4f}"
        if f < 1:
            return f"{f:.3f}"
        return f"{f:.2f}"
    except Exception:
        return val


def prom_label_str(labels: dict) -> str:
    filtered = {k: v for k, v in labels.items() if k not in ("__name__",)}
    if not filtered:
        return ""
    return "{" + ", ".join(f'{k}="{v}"' for k, v in list(filtered.items())[:4]) + "}"


def build_body(conn_err=None):
    if conn_err:
        return f"""<div class="error-box">
          <div class="icon">⚠️</div>
          <p>Could not reach Prometheus. Make sure port-forward is running:</p>
          <p style="margin-top:10px;"><code>kubectl -n prometheus port-forward svc/prometheus 9090:9090</code></p>
          <p style="margin-top:8px;color:#f85149">{conn_err}</p>
        </div>"""

    cards_html = '<div class="cards">'
    all_raw = []

    for i, (label, expr, unit) in enumerate(QUERIES):
        color = CARD_COLORS[i % len(CARD_COLORS)]
        results, err = prom_query(expr)
        if err or results is None:
            value_str = "N/A"
            css_class = "no-data"
        elif len(results) == 0:
            value_str = "—"
            css_class = "no-data"
        else:
            # Sum all result values for aggregate display
            total = 0.0
            for r in results:
                try:
                    total += float(r["value"][1])
                    all_raw.append((label, r.get("metric", {}), r["value"][1]))
                except Exception:
                    pass
            value_str = fmt_value(str(total))
            css_class = ""

        cards_html += f"""<div class="card {css_class}" style="--card-color:{color}">
          <div class="label">{label}</div>
          <div class="value">{value_str}</div>
          <div class="unit">{unit}</div>
          <div class="query-hint">{expr}</div>
        </div>"""

    cards_html += "</div>"

    # Raw table
    ts_now = datetime.now(tz=timezone.utc).strftime("%H:%M:%S UTC")
    raw_html = f"""
    <div class="section-header">
      <h2>All Metric Results</h2>
      <span class="refresh-badge">Fetched at {ts_now}</span>
    </div>
    <div class="raw-table-wrap">
      <table>
        <thead><tr>
          <th>Metric Group</th>
          <th>Labels</th>
          <th style="text-align:right">Value</th>
        </tr></thead>
        <tbody>"""
    if all_raw:
        for name, labels, val in all_raw:
            lbl = prom_label_str(labels)
            raw_html += f"""<tr>
              <td class="td-metric">{name}</td>
              <td class="td-label">{lbl or "—"}</td>
              <td class="td-value">{fmt_value(val)}</td>
            </tr>"""
    else:
        raw_html += '<tr><td colspan="3" style="text-align:center;padding:24px;color:var(--muted)">No raw results available</td></tr>'
    raw_html += "</tbody></table></div>"

    section_header = f"""<div class="section-header">
      <h2>Key Metrics</h2>
      <span class="refresh-badge">Auto-refresh every {REFRESH_SECONDS}s</span>
    </div>"""

    return section_header + cards_html + raw_html


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204); self.end_headers(); return

        # Quick connectivity test
        conn_err = None
        try:
            urllib.request.urlopen(f"{PROMETHEUS_URL}/-/healthy", timeout=3)
        except Exception as e:
            conn_err = str(e)

        body = build_body(conn_err)
        page = HTML_TEMPLATE.format(refresh=REFRESH_SECONDS, body=body, port=DASHBOARD_PORT)
        encoded = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", DASHBOARD_PORT), MetricsHandler)
    print(f"  ✅  Metrics Dashboard  →  http://localhost:{DASHBOARD_PORT}")
    print(f"      Source: Prometheus at {PROMETHEUS_URL}")
    print(f"      Refreshes every {REFRESH_SECONDS}s  |  Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMetrics dashboard stopped.")
