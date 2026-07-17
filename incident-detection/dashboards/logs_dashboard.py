#!/usr/bin/env python3
"""
logs_dashboard.py
=================
Serves a live Logs dashboard on http://localhost:5001

Queries Loki's HTTP API and renders a dark-themed auto-refreshing page
showing the latest log lines from the Kubernetes microservices,
colour-coded by severity.

Backend: Loki :3100 (logs come from Promtail → Loki)

Usage:
    python3 logs_dashboard.py

Prerequisites:
    kubectl -n default port-forward svc/loki 3100:3100
"""

import json
import urllib.request
import urllib.parse
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

LOKI_URL = "http://localhost:3100"
DASHBOARD_PORT = 5001
REFRESH_SECONDS = 10

# Promtail uses job=kubernetes-pods for all pod logs
LOKI_QUERY = '{job=~"kubernetes-pods.*"}'

# ─── HTML template ────────────────────────────────────────────────────────────
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Causa · Logs Dashboard</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
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
      --purple: #bc8cff;
      --text: #e6edf3;
      --muted: #8b949e;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }}
    header {{
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 16px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 10;
    }}
    header h1 {{
      font-size: 1.3rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .badge {{
      background: linear-gradient(135deg, #238636, #2ea043);
      color: #fff;
      font-size: 0.7rem;
      font-weight: 600;
      padding: 3px 10px;
      border-radius: 20px;
      letter-spacing: 0.05em;
    }}
    .meta {{
      font-size: 0.8rem;
      color: var(--muted);
    }}
    .meta span {{
      color: var(--accent);
    }}
    main {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 28px 32px;
    }}
    .stats-bar {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 28px;
    }}
    .stat-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px 20px;
    }}
    .stat-card .label {{
      font-size: 0.72rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }}
    .stat-card .value {{
      font-size: 1.8rem;
      font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
    }}
    .v-green {{ color: var(--green); }}
    .v-yellow {{ color: var(--yellow); }}
    .v-red {{ color: var(--red); }}
    .v-accent {{ color: var(--accent); }}
    .section-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 14px;
    }}
    .section-header h2 {{
      font-size: 1rem;
      font-weight: 600;
    }}
    .refresh-badge {{
      font-size: 0.72rem;
      color: var(--muted);
      background: var(--surface2);
      border: 1px solid var(--border);
      padding: 3px 10px;
      border-radius: 20px;
    }}
    .log-table-wrap {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
    }}
    .log-table {{
      width: 100%;
      border-collapse: collapse;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.78rem;
    }}
    .log-table thead tr {{
      background: var(--surface2);
      border-bottom: 1px solid var(--border);
    }}
    .log-table th {{
      padding: 10px 16px;
      text-align: left;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      font-weight: 600;
    }}
    .log-table tbody tr {{
      border-bottom: 1px solid var(--border);
      transition: background 0.15s;
    }}
    .log-table tbody tr:last-child {{ border-bottom: none; }}
    .log-table tbody tr:hover {{ background: var(--surface2); }}
    .log-table td {{
      padding: 9px 16px;
      vertical-align: top;
      line-height: 1.55;
    }}
    .td-time {{ white-space: nowrap; color: var(--muted); }}
    .td-stream {{ white-space: nowrap; }}
    .td-msg {{ word-break: break-all; max-width: 700px; }}
    .level-error {{ color: var(--red); }}
    .level-warn  {{ color: var(--yellow); }}
    .level-info  {{ color: var(--green); }}
    .level-debug {{ color: var(--purple); }}
    .level-other {{ color: var(--muted); }}
    .dot {{
      display: inline-block;
      width: 7px; height: 7px;
      border-radius: 50%;
      margin-right: 6px;
      vertical-align: middle;
    }}
    .dot-error {{ background: var(--red); }}
    .dot-warn  {{ background: var(--yellow); }}
    .dot-info  {{ background: var(--green); }}
    .dot-debug {{ background: var(--purple); }}
    .dot-other {{ background: var(--muted); }}
    .pulse {{
      display: inline-block;
      width: 8px; height: 8px;
      border-radius: 50%;
      background: var(--green);
      animation: pulse 2s infinite;
      margin-right: 8px;
    }}
    @keyframes pulse {{
      0%, 100% {{ opacity: 1; transform: scale(1); }}
      50% {{ opacity: 0.5; transform: scale(1.3); }}
    }}
    .error-box {{
      text-align: center;
      padding: 48px 0;
      color: var(--muted);
    }}
    .error-box .icon {{ font-size: 2.5rem; margin-bottom: 12px; }}
    .error-box p {{ font-size: 0.85rem; }}
    .error-box code {{
      background: var(--surface2);
      padding: 2px 8px;
      border-radius: 4px;
      color: var(--accent);
    }}
    footer {{
      text-align: center;
      padding: 24px;
      font-size: 0.75rem;
      color: var(--muted);
      border-top: 1px solid var(--border);
      margin-top: 40px;
    }}
    #countdown {{
      font-weight: 600;
      color: var(--accent);
    }}
  </style>
</head>
<body>
<header>
  <h1>
    <span class="pulse"></span>
    Causa <span style="color:var(--muted);font-weight:300;margin:0 4px;">·</span> Logs Dashboard
    <span class="badge">LIVE</span>
  </h1>
  <div class="meta">Source: <span>Loki :3100</span> &nbsp;|&nbsp; Refreshes in <span id="countdown">{refresh}s</span></div>
</header>

<main>
  <div class="stats-bar">
    <div class="stat-card">
      <div class="label">Total Lines</div>
      <div class="value v-accent">{total}</div>
    </div>
    <div class="stat-card">
      <div class="label">Error</div>
      <div class="value v-red">{count_error}</div>
    </div>
    <div class="stat-card">
      <div class="label">Warning</div>
      <div class="value v-yellow">{count_warn}</div>
    </div>
    <div class="stat-card">
      <div class="label">Info</div>
      <div class="value v-green">{count_info}</div>
    </div>
  </div>

  <div class="section-header">
    <h2>Recent Log Stream</h2>
    <span class="refresh-badge">Auto-refresh every {refresh}s</span>
  </div>

  <div class="log-table-wrap">
    {table_content}
  </div>
</main>

<footer>
  Causa · Stage 1 Incident Detection &nbsp;|&nbsp; Logs Dashboard · Port {port}
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


def query_loki():
    """Fetch the last 150 log lines from all Promtail kubernetes-pod streams."""
    import time
    now_ns = int(time.time() * 1e9)
    start_ns = now_ns - 60 * 60 * int(1e9)  # last 1 hour

    params = urllib.parse.urlencode({
        "query": LOKI_QUERY,
        "limit": 150,
        "start": start_ns,
        "end": now_ns,
        "direction": "backward",
    })
    url = f"{LOKI_URL}/loki/api/v1/query_range?{params}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode()), None
    except Exception as exc:
        return None, str(exc)


def level_class(line: str) -> str:
    lo = line.lower()
    if "error" in lo or "fatal" in lo or "critical" in lo:
        return "error"
    if "warn" in lo:
        return "warn"
    if "debug" in lo or "trace" in lo:
        return "debug"
    return "info"


def fmt_time(ts_ns: str) -> str:
    try:
        ts = int(ts_ns) / 1e9
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%H:%M:%S.%f")[:-3] + " UTC"
    except Exception:
        return ts_ns[:19]


def build_table(data) -> tuple:
    if data is None:
        return ("""<div class="error-box">
          <div class="icon">⚠️</div>
          <p>Could not reach Loki. Make sure port-forward is running:</p>
          <p style="margin-top:10px;"><code>kubectl -n loki port-forward svc/loki 3100:3100</code></p>
        </div>""", 0, 0, 0, 0)

    rows = []
    total = count_error = count_warn = count_info = 0

    result_streams = data.get("data", {}).get("result", [])
    for stream in result_streams:
        stream_labels = stream.get("stream", {})
        # Prefer more specific labels from Promtail
        job = (stream_labels.get("app")
               or stream_labels.get("pod", "").split("-")[0]
               or stream_labels.get("job", "—"))
        for ts_ns, line in stream.get("values", []):
            lvl = level_class(line)
            total += 1
            if lvl == "error":
                count_error += 1
            elif lvl == "warn":
                count_warn += 1
            else:
                count_info += 1
            line_esc = (line.replace("&", "&amp;").replace("<", "&lt;")
                            .replace(">", "&gt;").replace('"', "&quot;"))
            rows.append((int(ts_ns), fmt_time(ts_ns), job, line_esc, lvl))

    rows.sort(key=lambda r: r[0], reverse=True)

    if not rows:
        return ("""<div class="error-box">
          <div class="icon">📭</div>
          <p>No log data found yet. Promtail may still be catching up.</p>
          <p style="margin-top:10px;">Try: <code>kubectl -n default port-forward svc/loki 3100:3100</code></p>
        </div>""", 0, 0, 0, 0)

    html = """<table class="log-table">
      <thead><tr>
        <th>Time (UTC)</th>
        <th>Job / Stream</th>
        <th>Level</th>
        <th>Message</th>
      </tr></thead>
      <tbody>"""
    for _, t_str, job, line_esc, lvl in rows:
        dot = f'<span class="dot dot-{lvl}"></span>'
        html += f"""<tr>
          <td class="td-time">{t_str}</td>
          <td class="td-stream">{job}</td>
          <td class="level-{lvl}">{dot}{lvl.upper()}</td>
          <td class="td-msg">{line_esc}</td>
        </tr>"""
    html += "</tbody></table>"
    return html, total, count_error, count_warn, count_info


class LogsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        data, err = query_loki()
        table_content, total, count_error, count_warn, count_info = build_table(data)

        page = HTML_TEMPLATE.format(
            refresh=REFRESH_SECONDS,
            total=total,
            count_error=count_error,
            count_warn=count_warn,
            count_info=count_info,
            table_content=table_content,
            port=DASHBOARD_PORT,
        )
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # silence default access log


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", DASHBOARD_PORT), LogsHandler)
    print(f"  ✅  Logs Dashboard  →  http://localhost:{DASHBOARD_PORT}")
    print(f"      Source: Loki at {LOKI_URL}")
    print(f"      Refreshes every {REFRESH_SECONDS}s  |  Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLogs dashboard stopped.")
