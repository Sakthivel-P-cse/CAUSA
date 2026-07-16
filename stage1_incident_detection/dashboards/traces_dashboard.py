#!/usr/bin/env python3
"""
traces_dashboard.py
===================
Serves a live Traces dashboard on http://localhost:5003

Queries Tempo's HTTP API for recent traces.

Backend: Tempo :3200 (traces via OTel Collector → Tempo)

Usage:
    python3 traces_dashboard.py

Prerequisites:
    kubectl -n tempo port-forward svc/tempo 3200:3200
"""

import json
import urllib.request
import urllib.parse
import urllib.error
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

TEMPO_URL = "http://localhost:3200"
DASHBOARD_PORT = 5003
REFRESH_SECONDS = 10


# ─── Helpers ──────────────────────────────────────────────────────────────────

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def dur_fmt(us):
    ms = us / 1000.0
    if ms >= 1000:
        return "%.2fs" % (ms / 1000.0), "dur-slow"
    if ms >= 200:
        return "%.1fms" % ms, "dur-mid"
    return "%.1fms" % ms, "dur-fast"


def fmt_ts(us):
    try:
        dt = datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc)
        return dt.strftime("%H:%M:%S UTC")
    except Exception:
        return "—"


# ─── Tempo API ────────────────────────────────────────────────────────────────

def fetch_recent_traces(limit=30):
    """Call Tempo's /api/search endpoint to get recent traces."""
    end_ns = int(time.time())
    start_ns = end_ns - 3600
    params = urllib.parse.urlencode({
        "start": start_ns,
        "end": end_ns,
        "limit": limit,
    })
    url = TEMPO_URL + "/api/search?" + params
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data.get("traces", []), None
    except Exception as exc:
        return [], str(exc)


def fetch_trace_detail(trace_id):
    """Fetch full span detail for a single trace from Tempo."""
    url = TEMPO_URL + "/api/traces/" + trace_id
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode()), None
    except Exception as exc:
        return None, str(exc)


# ─── HTML builder ─────────────────────────────────────────────────────────────

CSS = """
    :root {
      --bg: #0d1117; --surface: #161b22; --surface2: #21262d; --border: #30363d;
      --accent: #bc8cff; --blue: #58a6ff; --green: #3fb950; --yellow: #d29922;
      --red: #f85149; --text: #e6edf3; --muted: #8b949e;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
    header {
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 16px 32px; display: flex; align-items: center; justify-content: space-between;
      position: sticky; top: 0; z-index: 10;
    }
    header h1 { font-size: 1.3rem; font-weight: 700; display: flex; align-items: center; gap: 10px; }
    .badge { background: linear-gradient(135deg, #6e40c9, #9b72eb); color: #fff; font-size: 0.7rem; font-weight: 600; padding: 3px 10px; border-radius: 20px; }
    .meta { font-size: 0.8rem; color: var(--muted); }
    .meta span { color: var(--accent); }
    main { max-width: 1400px; margin: 0 auto; padding: 28px 32px; }
    .stats-bar { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 28px; }
    .stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px; }
    .stat-card .label { font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
    .stat-card .value { font-size: 1.8rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
    .v-purple { color: var(--accent); } .v-blue { color: var(--blue); }
    .v-green  { color: var(--green); } .v-red   { color: var(--red); }
    .section-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
    .section-header h2 { font-size: 1rem; font-weight: 600; }
    .refresh-badge { font-size: 0.72rem; color: var(--muted); background: var(--surface2); border: 1px solid var(--border); padding: 3px 10px; border-radius: 20px; }
    .services-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }
    .svc-chip { background: rgba(88,166,255,0.1); border: 1px solid rgba(88,166,255,0.25); color: var(--blue); font-size: 0.75rem; padding: 4px 12px; border-radius: 20px; }
    .table-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
    table { width: 100%; border-collapse: collapse; }
    thead tr { background: var(--surface2); border-bottom: 1px solid var(--border); }
    th { padding: 10px 16px; text-align: left; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); font-weight: 600; }
    tbody tr { border-bottom: 1px solid var(--border); transition: background 0.15s; }
    tbody tr:last-child { border-bottom: none; }
    tbody tr:hover { background: var(--surface2); }
    td { padding: 10px 16px; font-size: 0.8rem; vertical-align: middle; }
    .td-id { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; }
    .td-id a { color: var(--accent); text-decoration: none; border-bottom: 1px dashed var(--border); }
    .td-id a:hover { color: #fff; border-color: var(--accent); }
    .td-svc { font-weight: 500; }
    .td-dur { font-family: 'JetBrains Mono', monospace; text-align: right; white-space: nowrap; }
    .td-time { color: var(--muted); font-size: 0.75rem; white-space: nowrap; }
    .dur-fast { color: var(--green); } .dur-mid { color: var(--yellow); } .dur-slow { color: var(--red); }
    .service-tag { display: inline-block; background: rgba(88,166,255,0.12); border: 1px solid rgba(88,166,255,0.25); color: var(--blue); font-size: 0.7rem; padding: 2px 8px; border-radius: 20px; }
    .spans-badge { display: inline-block; background: var(--surface2); border: 1px solid var(--border); color: var(--muted); font-size: 0.68rem; padding: 1px 8px; border-radius: 20px; margin-left: 6px; font-family: 'JetBrains Mono', monospace; }
    .pulse { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); animation: pulse 2s infinite; margin-right: 8px; }
    @keyframes pulse { 0%,100% { opacity:1; transform:scale(1); } 50% { opacity:0.5; transform:scale(1.3); } }
    .error-box { text-align: center; padding: 48px 0; color: var(--muted); }
    .error-box .icon { font-size: 2.5rem; margin-bottom: 12px; }
    .error-box p { font-size: 0.85rem; margin-top: 8px; }
    .error-box code { background: var(--surface2); padding: 2px 8px; border-radius: 4px; color: var(--accent); }
    footer { text-align: center; padding: 24px; font-size: 0.75rem; color: var(--muted); border-top: 1px solid var(--border); margin-top: 40px; }
    #countdown { font-weight: 600; color: var(--accent); }
"""

JS = """
  let t = REFRESH_VAL;
  const el = document.getElementById('countdown');
  const _timer = setInterval(() => { t--; if (t <= 0) { clearInterval(_timer); location.reload(); return; } el.textContent = t + 's'; }, 1000);
""".replace("REFRESH_VAL", str(REFRESH_SECONDS))


def make_html(total, n_services, avg_dur, slow, chips_html, content_html, fetched_at):
    parts = []
    parts.append("<!DOCTYPE html><html lang='en'><head>")
    parts.append("<meta charset='UTF-8'/>")
    parts.append("<meta name='viewport' content='width=device-width, initial-scale=1.0'/>")
    parts.append("<title>Causa · Traces Dashboard</title>")
    parts.append("<link href='https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&family=JetBrains+Mono:wght@400;500&display=swap' rel='stylesheet'/>")
    parts.append("<style>" + CSS + "</style></head><body>")

    # Header
    parts.append("<header><h1>")
    parts.append("<span class='pulse'></span>")
    parts.append("Causa <span style='color:var(--muted);font-weight:300;margin:0 4px;'>·</span> Traces Dashboard ")
    parts.append("<span class='badge'>LIVE</span></h1>")
    parts.append("<div class='meta'>Source: <span>Tempo :3200</span> &nbsp;|&nbsp; ")
    parts.append("Refreshes in <span id='countdown'>" + str(REFRESH_SECONDS) + "s</span></div>")
    parts.append("</header>")

    # Main
    parts.append("<main>")

    # Stats bar
    parts.append("<div class='stats-bar'>")
    parts.append("<div class='stat-card'><div class='label'>Total Traces</div><div class='value v-purple'>" + str(total) + "</div></div>")
    parts.append("<div class='stat-card'><div class='label'>Services</div><div class='value v-blue'>" + str(n_services) + "</div></div>")
    parts.append("<div class='stat-card'><div class='label'>Avg Duration</div><div class='value v-green'>" + esc(avg_dur) + "</div></div>")
    parts.append("<div class='stat-card'><div class='label'>Slow (&gt;1s)</div><div class='value v-red'>" + str(slow) + "</div></div>")
    parts.append("</div>")

    parts.append(chips_html)

    parts.append("<div class='section-header'>")
    parts.append("<h2>Recent Traces</h2>")
    parts.append("<span class='refresh-badge'>Auto-refresh every " + str(REFRESH_SECONDS) + "s · " + fetched_at + "</span>")
    parts.append("</div>")

    parts.append("<div class='table-wrap'>" + content_html + "</div>")
    parts.append("</main>")

    parts.append("<footer>Causa · Stage 1 Incident Detection &nbsp;|&nbsp; Traces Dashboard · Port " + str(DASHBOARD_PORT) + "</footer>")
    parts.append("<script>" + JS + "</script>")
    parts.append("</body></html>")

    return "".join(parts)


def build_page():
    fetched_at = datetime.now(tz=timezone.utc).strftime("%H:%M:%S UTC")

    # Connectivity check via /ready
    conn_err = None
    try:
        urllib.request.urlopen(TEMPO_URL + "/ready", timeout=3)
    except Exception as e:
        conn_err = str(e)

    if conn_err:
        err_html = (
            "<div class='error-box'><div class='icon'>⚠️</div>"
            "<p>Could not reach Tempo. Make sure port-forward is running:</p>"
            "<p><code>kubectl -n tempo port-forward svc/tempo 3200:3200</code></p>"
            "<p style='color:#f85149'>" + esc(conn_err) + "</p></div>"
        )
        return make_html(0, 0, "—", 0, "", err_html, fetched_at)

    traces, err = fetch_recent_traces(limit=30)
    if not traces:
        msg = esc(err) if err else "Send traffic via the OTel client to generate traces first."
        no_traces = (
            "<div class='error-box'><div class='icon'>📭</div>"
            "<p>No recent traces found in Tempo.</p>"
            "<p>" + msg + "</p></div>"
        )
        return make_html(0, 0, "—", 0, "", no_traces, fetched_at)

    # Tempo /api/search returns lightweight trace summaries — use them directly
    # Each item: {traceID, rootServiceName, rootTraceName, startTimeUnixNano, durationMs, spanSets}
    all_rows = []
    services_seen = set()
    for t in traces:
        trace_id   = t.get("traceID", "")
        svc        = t.get("rootServiceName") or t.get("rootServiceName", "unknown")
        op_name    = t.get("rootTraceName") or "—"
        dur_ms     = t.get("durationMs", 0)
        start_nano = int(t.get("startTimeUnixNano", 0))
        start_us   = start_nano // 1000
        dur_us     = dur_ms * 1000
        span_count = t.get("spanCount", "—")
        services_seen.add(svc)
        all_rows.append((start_us, trace_id, svc, op_name, dur_us, span_count))

    all_rows.sort(key=lambda r: r[0], reverse=True)

    total    = len(all_rows)
    slow     = sum(1 for r in all_rows if r[4] >= 1_000_000)
    durations = [r[4] for r in all_rows if r[4] > 0]
    avg_us   = int(sum(durations) / len(durations)) if durations else 0
    avg_dur, _ = dur_fmt(avg_us) if durations else ("—", "")

    # Service chips
    chips = "<div class='services-row'>"
    for svc in sorted(services_seen):
        chips += "<span class='svc-chip'>" + esc(svc) + "</span>"
    chips += "</div>"

    # Table
    rows_html = ""
    for start_us, trace_id, svc, op_name, dur_us, span_count in all_rows[:30]:
        dur_str, dur_css = dur_fmt(dur_us) if dur_us > 0 else ("—", "")
        ts_str   = fmt_ts(start_us)
        short_id = trace_id[:16] + "…" if len(trace_id) > 16 else trace_id
        tempo_ui = "http://localhost:3000/explore?datasource=tempo&traceId=" + trace_id
        rows_html += (
            "<tr>"
            "<td class='td-id'><a href='" + tempo_ui + "' target='_blank' title='" + trace_id + "'>" + esc(short_id) + "</a></td>"
            "<td class='td-svc'><span class='service-tag'>" + esc(svc) + "</span></td>"
            "<td>" + esc(op_name) + ("<span class='spans-badge'>" + str(span_count) + " spans</span>" if span_count != "—" else "") + "</td>"
            "<td class='td-dur " + dur_css + "'>" + dur_str + "</td>"
            "<td class='td-time'>" + ts_str + "</td>"
            "</tr>"
        )

    if not rows_html:
        rows_html = "<tr><td colspan='5' style='text-align:center;padding:24px;color:var(--muted)'>No recent traces in the last hour.</td></tr>"

    table = (
        "<table>"
        "<thead><tr><th>Trace ID</th><th>Service</th><th>Operation</th>"
        "<th style='text-align:right'>Duration</th><th>Start Time</th></tr></thead>"
        "<tbody>" + rows_html + "</tbody>"
        "</table>"
    )

    return make_html(total, len(services_seen), avg_dur, slow, chips, table, fetched_at)


# ─── HTTP server ──────────────────────────────────────────────────────────────

class TracesHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        page = build_page()
        encoded = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", DASHBOARD_PORT), TracesHandler)
    print("  \u2705  Traces Dashboard  \u2192  http://localhost:%d" % DASHBOARD_PORT)
    print("      Source: Tempo at %s" % TEMPO_URL)
    print("      Refreshes every %ds  |  Press Ctrl+C to stop\n" % REFRESH_SECONDS)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTraces dashboard stopped.")
