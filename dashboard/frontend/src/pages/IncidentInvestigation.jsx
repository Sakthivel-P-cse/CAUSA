import React, { useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useFetch } from '../hooks/useFetch.js'
import { apiFetch } from '../api/client.js'
import { isAbstainedSummary, normalizeReport } from '../api/incidents.js'
import { buildRcaNarrative } from '../api/rcaNarrative.js'
import { Spinner, ErrorMsg } from '../components/Common.jsx'
import AnomalySparkline from '../components/watchdog/AnomalySparkline.jsx'
import ConfidenceBadge from '../components/watchdog/ConfidenceBadge.jsx'
import RcaStory from '../components/watchdog/RcaStory.jsx'
import TopologyGraph from '../components/TopologyGraph.jsx'

function serviceQs(baseQs, node) {
  const params = new URLSearchParams(baseQs)
  if (node) params.set('service', node)
  return params.toString()
}

function looksLikeService(node) {
  const value = String(node || '')
  if (!value || value.includes('/') || value.includes('control-plane')) return false
  return true
}

const BASE_TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'topology', label: 'Topology' },
  { id: 'telemetry', label: 'Related telemetry' },
  { id: 'remediation', label: 'Remediation' },
]

const COVERAGE_TILES = [
  ['edge_count', 'Edges'],
  ['topology_edge_count', 'Topology edges'],
  ['span_count', 'Spans'],
  ['log_record_count', 'Logs'],
  ['metric_point_count', 'Metrics'],
  ['infra_node_count', 'Infra nodes'],
]

function SignalBars({ signals = {} }) {
  const rows = [
    ['T', 'Temporal (onset)', signals.T],
    ['D', 'Direction (influence)', signals.D],
    ['P', 'Propagation', signals.P],
  ]
  return (
    <div className="signal-bars" aria-label="T D P signals">
      {rows.map(([label, title, value]) => {
        const n = Math.max(0, Math.min(1, Number(value) || 0))
        return (
          <div className="signal-bar" key={label} title={title}>
            <span>{label}</span>
            <div className="signal-bar__track"><div className="signal-bar__fill" style={{ width: `${n * 100}%` }} /></div>
            <em>{n.toFixed(2)}</em>
          </div>
        )
      })}
      <p className="signal-bars__legend muted">T = timing · D = influence direction · P = propagation</p>
    </div>
  )
}

function CandidatesList({ candidates, abstained, summary, windowQs }) {
  if (!candidates?.length) {
    return (
      <p className="muted empty-hint">
        {abstained
          ? `No origin candidates — ${summary || 'RCA abstained'}.`
          : 'No origin candidates recorded.'}
      </p>
    )
  }
  const ranked = [...candidates].sort((a, b) => Number(b.score || 0) - Number(a.score || 0))
  return (
    <ul className="candidate-list">
      {ranked.map((c, idx) => (
        <li className="candidate-card" key={`${c.node_id}-${c.role}-${idx}`}>
          <div className="candidate-card__head">
            <strong>{c.node_id || 'node'}</strong>
            <span className="status-chip">{c.role || 'candidate'}</span>
            <span className="candidate-card__kind">{c.node_kind || '—'}</span>
          </div>
          <p className="candidate-card__score">
            score {Number(c.score || 0).toFixed(3)}
          </p>
          <SignalBars signals={c.signals} />
          <p>{c.explanation || '—'}</p>
          {idx === 0 && looksLikeService(c.node_id) && (
            <div className="rca-story__pivots">
              <Link to={`/metrics?${serviceQs(windowQs, c.node_id)}`}>Metrics</Link>
              <Link to={`/logs?${serviceQs(windowQs, c.node_id)}`}>Logs</Link>
              <Link to={`/traces?${serviceQs(windowQs, c.node_id)}`}>Traces</Link>
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}

function TelemetryCoverage({ refs, report, windowQs }) {
  const traceId = report.trace_id || refs.trace_id
  const tiles = COVERAGE_TILES.filter(([key]) => refs[key] != null && refs[key] !== '')
  return (
    <div className="panel">
      <h2>Telemetry coverage</h2>
      <div className="coverage-tiles">
        {tiles.map(([key, label]) => (
          <div className="coverage-tile" key={key}>
            <span>{label}</span>
            <strong>{String(refs[key])}</strong>
          </div>
        ))}
        {traceId && (
          <div className="coverage-tile coverage-tile--wide">
            <span>Trace ID</span>
            <strong className="mono">{traceId}</strong>
          </div>
        )}
      </div>
      {!tiles.length && !traceId && <p className="muted">No coverage counters for this report.</p>}
      <div className="telemetry-pivots telemetry-pivots--inline">
        <Link to={`/metrics?${windowQs}`}>Metrics</Link>
        <Link to={`/logs?${windowQs}`}>Logs</Link>
        <Link to={`/traces?${windowQs}`}>Traces</Link>
        {traceId && (
          <Link to={`/traces?trace=${encodeURIComponent(traceId)}`}>Open trace</Link>
        )}
      </div>
    </div>
  )
}

function RemediationLiveTab({ report }) {
  const incidentKey = report.incident_id || report.id
  const plans = useFetch(
    incidentKey ? `/api/remediation/plans?incident_id=${encodeURIComponent(incidentKey)}&limit=20` : null,
    [incidentKey],
  )
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)

  const propose = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const res = await apiFetch('/api/remediation/propose', {
        method: 'POST',
        body: { incident_id: incidentKey },
      })
      setMsg(`Proposed ${res?.plan?.fault_class || 'plan'} (${res?.source || res?.plan?.details?.source || 'rules'})`)
      await plans.reload()
    } catch (err) {
      setMsg(err?.message || String(err))
    } finally {
      setBusy(false)
    }
  }

  const items = plans.data?.items || []

  return (
    <div className="tab-panel">
      <div className="panel">
        <h2>Remediation</h2>
        <p className="muted">Classify with rules + NVIDIA NIM, then approve a fix from the Remediation workspace.</p>
        <div className="chip-row remediation-actions">
          <button type="button" className="btn primary" disabled={busy || !incidentKey} onClick={propose}>
            {busy ? 'Proposing…' : 'Propose fix'}
          </button>
          <Link className="btn-link" to="/remediation">Open queue</Link>
        </div>
        {msg && <p className="muted">{msg}</p>}
        {plans.loading && !plans.data && <Spinner label="Loading plans" />}
        {items.length === 0 && !plans.loading && (
          <p className="empty-hint">No live plans yet — click Propose fix.</p>
        )}
        <ul className="evidence-list">
          {items.map(plan => (
            <li key={plan.id} className="evidence-block">
              <div className="evidence-head">
                <strong>{plan.fault_class}</strong>
                <span className="status-chip">{plan.status}</span>
                <span className="status-chip">{plan.details?.source || 'rules'}</span>
              </div>
              <p>{plan.details?.rationale || plan.summary}</p>
              <div className="rca-story__pivots">
                <Link to={`/remediation/${encodeURIComponent(plan.id)}`}>Review & approve</Link>
              </div>
            </li>
          ))}
        </ul>
        {report.remediation?.parsed && (
          <details className="legacy-artifact">
            <summary>Legacy file artifact</summary>
            <pre className="code-block">{JSON.stringify(report.remediation, null, 2)}</pre>
          </details>
        )}
      </div>
    </div>
  )
}

export default function IncidentInvestigation() {
  const { reportId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const debug = searchParams.get('debug') === '1'
  const tab = searchParams.get('tab') || 'overview'
  const setTab = (next) => {
    const params = new URLSearchParams(searchParams)
    params.set('tab', next)
    setSearchParams(params, { replace: true })
  }

  const tabs = debug ? [...BASE_TABS, { id: 'raw', label: 'Raw' }] : BASE_TABS

  const { data, loading, error } = useFetch(
    `/api/incidents/${encodeURIComponent(reportId)}`,
    [reportId],
  )
  const report = useMemo(() => (data ? normalizeReport(data) : null), [data])
  const graphUrl = tab === 'topology'
    ? `/api/graphs/incident?report_id=${encodeURIComponent(reportId)}`
    : null
  const graph = useFetch(graphUrl, [tab, reportId])

  if (loading && !report) return <Spinner label="Loading investigation" />
  if (error && !report) return <ErrorMsg error={error} />
  if (!report) return <ErrorMsg error={{ message: 'Report not found' }} />

  const anomalyValues = (report.anomaly_score_list || []).map(row => Number(row.score || 0))
  const abstained = isAbstainedSummary(report.root_cause_summary)
  const narrative = buildRcaNarrative(report, { abstained })
  const failureLabel = narrative.failureLabel
  const refs = report.telemetry_refs || {}
  const windowQs = new URLSearchParams()
  if (report.window_start) windowQs.set('from', report.window_start)
  if (report.window_end) windowQs.set('to', report.window_end)
  if (report.alerted_service) windowQs.set('service', report.alerted_service)
  const hasAnomalies = (report.anomaly_score_list || []).length > 0
  const showBanner = abstained || narrative.limited_detail

  return (
    <div className="watchdog-page investigation-page">
      <header className="investigation-header">
        <div>
          <p className="eyebrow">
            <Link to="/incidents">Incidents</Link> / {report.incident_id || report.id}
          </p>
          <h1>{report.alert_name || 'RCA report'}</h1>
          <p>{narrative.headline}</p>
          <div className="chip-row">
            <span className={`status-chip status-${report.severity}`}>{report.severity}</span>
            <span className="status-chip">{failureLabel}</span>
            <span className="status-chip">{report.incident_status}</span>
            {report.root_cause_service && <span className="status-chip">root: {report.root_cause_service}</span>}
          </div>
        </div>
        <ConfidenceBadge
          score={report.confidence_score}
          limited={!!narrative.limited_detail}
          showMeter
        />
      </header>

      {showBanner && (
        <div className="analysis-warning" role="status">
          <strong>{abstained ? 'RCA abstained' : 'Weak signal'}</strong>
          <p>{narrative.limited_reason || narrative.confidence_label}</p>
        </div>
      )}

      <div className="investigation-tabs" role="tablist">
        {tabs.map(item => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="tab-panel investigation-overview">
          <RcaStory
            report={report}
            windowQs={windowQs}
            abstained={abstained}
            onRemediate={() => setTab('remediation')}
          />

          <details className="fold-panel">
            <summary>Origin candidates (T / D / P)</summary>
            <CandidatesList
              candidates={report.origin_candidates}
              abstained={abstained}
              summary={report.root_cause_summary}
              windowQs={windowQs}
            />
          </details>

          {hasAnomalies && (
            <details className="fold-panel">
              <summary>Anomaly scores</summary>
              {anomalyValues.length >= 2 && <AnomalySparkline values={anomalyValues} />}
              <ul className="compact-list">
                {(report.anomaly_score_list || []).slice(0, 8).map(row => (
                  <li key={row.node}>{row.node}: {Number(row.score).toFixed(3)}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {tab === 'evidence' && (
        <div className="tab-panel">
          <div className="panel">
            <h2>Evidence</h2>
            {(report.evidence || []).length === 0 && <p className="muted">No evidence items.</p>}
            <ul className="evidence-list">
              {(report.evidence || []).map((ev, idx) => (
                <li key={`${ev.node}-${idx}`} className="evidence-block">
                  <div className="evidence-head">
                    <strong>{ev.node || 'node'}</strong>
                    <span>score {Number(ev.score || 0).toFixed(3)}</span>
                  </div>
                  <p>{ev.explanation || '—'}</p>
                  <SignalBars signals={ev.signals} />
                  {looksLikeService(ev.node) && (
                    <div className="rca-story__pivots">
                      <Link to={`/logs?${serviceQs(windowQs, ev.node)}`}>Open logs</Link>
                      <Link to={`/metrics?${serviceQs(windowQs, ev.node)}`}>Open metrics</Link>
                    </div>
                  )}
                  {ev.dev_vector && (
                    <pre className="code-block">{JSON.stringify(ev.dev_vector, null, 2)}</pre>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {tab === 'timeline' && (
        <div className="tab-panel">
          <div className="panel">
            <h2>Timeline</h2>
            {(report.timeline || []).length === 0 && <p className="muted">No timeline events.</p>}
            <ol className="timeline-list timeline-list--dense">
              {(report.timeline || []).map((ev, idx) => (
                <li key={idx} className="timeline-event">
                  <time>{ev.timestamp || '—'}</time>
                  <div className="timeline-event__body">
                    <strong>{ev.service || '—'}</strong>
                    <span className="timeline-event__kind">{ev.event_type || ev.kind || 'event'}</span>
                    <span>{ev.event || ev.message || JSON.stringify(ev)}</span>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}

      {tab === 'topology' && (
        <div className="tab-panel">
          <div className="panel">
            <h2>Incident topology</h2>
            {graph.loading && <Spinner label="Loading graph" />}
            {graph.error && <ErrorMsg error={graph.error} />}
            {graph.data && <TopologyGraph graph={graph.data} height={480} />}
            {!graph.loading && !graph.error && !graph.data?.nodes?.length && (
              <p className="muted">No topology nodes for this report.</p>
            )}
          </div>
        </div>
      )}

      {tab === 'telemetry' && (
        <div className="tab-panel">
          <TelemetryCoverage refs={refs} report={report} windowQs={windowQs} />
          <div className="panel explore-pivots-panel">
            <h2>Explore pivots</h2>
            <div className="telemetry-pivots">
              <Link to={`/metrics?${windowQs}`}><strong>Metrics</strong><span>Service signals in the incident window</span></Link>
              <Link to={`/logs?${windowQs}`}><strong>Logs</strong><span>Loki entries for the alerted service</span></Link>
              <Link to={`/traces?${windowQs}`}><strong>Traces</strong><span>Tempo search in the same window</span></Link>
              {report.trace_id && (
                <Link to={`/traces?trace=${encodeURIComponent(report.trace_id)}`}>
                  <strong>Trace {String(report.trace_id).slice(0, 12)}…</strong>
                  <span>Open persisted trace ID</span>
                </Link>
              )}
              <Link to={`/graphs?map=incident&report=${encodeURIComponent(report.id)}`}>
                <strong>Graph map</strong>
                <span>Incident subgraph</span>
              </Link>
            </div>
          </div>
        </div>
      )}

      {tab === 'remediation' && (
        <RemediationLiveTab report={report} />
      )}

      {tab === 'raw' && debug && (
        <div className="tab-panel">
          <div className="panel">
            <h2>Raw report JSON</h2>
            <pre className="code-block">{JSON.stringify(report, null, 2)}</pre>
          </div>
        </div>
      )}
    </div>
  )
}
