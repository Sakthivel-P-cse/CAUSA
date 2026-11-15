import React, { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useFetch } from '../hooks/useFetch.js'
import { apiFetch } from '../api/client.js'
import { formatAbstainReason, isAbstainedSummary, normalizeReport, parseAbstainReason } from '../api/incidents.js'
import { buildRcaNarrative } from '../api/rcaNarrative.js'
import { Spinner, ErrorMsg } from '../components/Common.jsx'
import AnomalySparkline from '../components/watchdog/AnomalySparkline.jsx'
import ConfidenceBadge from '../components/watchdog/ConfidenceBadge.jsx'
import TopologyGraph from '../components/TopologyGraph.jsx'
import InvestigationTimeline from '../components/watchdog/InvestigationTimeline.jsx'
import AbstainEvidencePanel from '../components/watchdog/AbstainEvidencePanel.jsx'

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
  { id: 'summary', label: 'Summary' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'hypotheses', label: 'Hypotheses' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'topology', label: 'Topology' },
  { id: 'telemetry', label: 'Telemetry' },
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
    ['T', 'Temporal', signals.T],
    ['D', 'Direction', signals.D],
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
    </div>
  )
}

function CandidatesList({ candidates, abstained, summary, windowQs }) {
  if (!candidates?.length) {
    const reason = abstained ? formatAbstainReason(parseAbstainReason(summary)) : null
    return (
      <p className="muted empty-hint">
        {abstained
          ? `No origin candidates — ${reason || 'RCA abstained'}.`
          : 'No origin candidates.'}
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
          <p className="candidate-card__score">score {Number(c.score || 0).toFixed(3)}</p>
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

function AnswerCard({ report, narrative, abstained, onAck, onRemediate, onOpenTab, ackBusy }) {
  const top = (report.origin_candidates || [])[0] || {}
  const hit = (report.blast_radius || []).length || (report.amplifier_nodes || []).length
  const retry = report.retry_amplification
  const abstainReason = parseAbstainReason(report.root_cause_summary)
  return (
    <section className="answer-card" aria-label="Root cause answer">
      <div className="answer-card__head">
        <span className="eyebrow">Root cause</span>
        <ConfidenceBadge
          score={report.confidence_score}
          limited={!!narrative.limited_detail}
          showMeter={!abstained}
          signals={top.signals}
          calibrated={abstained ? null : false}
          abstained={abstained}
          abstainReason={abstainReason}
        />
      </div>
      <h2>{abstained ? 'No root cause (abstained)' : (narrative.headline || report.root_cause_summary || '—')}</h2>
      {narrative.why && (
        <p className={`answer-card__why${abstained ? ' answer-card__why--abstain' : ''}`}>
          {narrative.why}
        </p>
      )}
      {abstained && onOpenTab && (
        <div className="answer-card__tab-links">
          <button type="button" className="btn-link" onClick={() => onOpenTab('evidence')}>View evidence</button>
          <button type="button" className="btn-link" onClick={() => onOpenTab('timeline')}>View timeline</button>
          <button type="button" className="btn-link" onClick={() => onOpenTab('hypotheses')}>View hypotheses</button>
        </div>
      )}
      <div className="chip-row">
        <span className="status-chip">{narrative.failureLabel}</span>
        {report.routing && <span className="status-chip">Route: {report.routing}</span>}
        {hit > 0 && <span className="status-chip">{hit} services in scope</span>}
        {report.lifecycle && <span className="status-chip">{report.lifecycle}</span>}
      </div>
      {retry && (
        <p className="muted">
          Retry amplification
          {typeof retry === 'object' ? `: ${JSON.stringify(retry).slice(0, 120)}` : ''}
        </p>
      )}
      <div className="answer-card__actions">
        <button type="button" className="btn primary" disabled={ackBusy} onClick={onAck}>
          {ackBusy ? 'Ack…' : 'Acknowledge'}
        </button>
        <button type="button" className="btn" onClick={onRemediate}>View remediation</button>
        <Link className="btn-link" to="/incidents">Back to inbox</Link>
      </div>
    </section>
  )
}

function RemediationLiveTab({ report }) {
  const incidentKey = report.incident_id || report.id
  const { data: plansData, reload: reloadPlans } = useFetch(
    incidentKey ? `/api/remediation/plans?incident_id=${encodeURIComponent(incidentKey)}&limit=20` : null,
    [incidentKey],
  )
  const [msg, setMsg] = useState(null)

  useEffect(() => {
    if (!incidentKey) return undefined
    let cancelled = false
    ;(async () => {
      try {
        const res = await apiFetch('/api/remediation/ensure', {
          method: 'POST',
          body: { incident_id: incidentKey },
        })
        if (cancelled) return
        if (res?.result?.auto_approved) setMsg('Remediation auto-proposed and queued for worker')
        else if (res?.result?.ok) setMsg('Remediation plan ready')
        else if (res?.result?.skipped) setMsg(null)
        await reloadPlans()
      } catch (err) {
        if (!cancelled) setMsg(err?.message || String(err))
      }
    })()
    return () => { cancelled = true }
  }, [incidentKey, reloadPlans])

  const items = useMemo(() => {
    const list = [...(plansData?.items || [])]
    list.sort((a, b) => (Date.parse(b.created_at || '') || 0) - (Date.parse(a.created_at || '') || 0))
    return list
  }, [plansData?.items])
  const latest = items[0]

  return (
    <div className="tab-panel">
      <div className="panel">
        <h2>Remediation</h2>
        <p className="muted">
          Plans are proposed from RCA. Executable ones may auto-approve and run via the worker;
          open a plan to review, approve, or refine with a manual prompt.
        </p>
        <div className="chip-row remediation-actions">
          <Link className="btn-link" to="/remediation">Open queue</Link>
          {latest && (
            <Link className="btn-link" to={`/remediation/${encodeURIComponent(latest.id)}`}>
              Open latest plan
            </Link>
          )}
        </div>
        {msg && <p className="muted">{msg}</p>}
        <ul className="evidence-list">
          {items.map(plan => {
            const sol = plan.details?.solution || plan.solution || {}
            const refined = Boolean(plan.details?.parent_plan_id || plan.details?.regen_of_plan_id)
            return (
              <li key={plan.id} className="evidence-block">
                <div className="evidence-head">
                  <strong>{plan.fault_class}</strong>
                  <span className="status-chip">{plan.status}</span>
                  {refined && <span className="status-chip">refined</span>}
                  {plan.details?.nim_prompt_source && (
                    <span className="status-chip">{plan.details.nim_prompt_source}</span>
                  )}
                </div>
                <p>{sol.summary || plan.details?.rationale || plan.summary || '—'}</p>
                {sol.diff && <pre className="code-block">{String(sol.diff).slice(0, 1200)}</pre>}
                <p className="muted">
                  {plan.decided_by === 'auto' ? 'Auto-approved · ' : ''}
                  {plan.action}
                </p>
                <Link to={`/remediation/${encodeURIComponent(plan.id)}`}>Open plan</Link>
              </li>
            )
          })}
          {items.length === 0 && <li className="muted">Generating remediation from RCA…</li>}
        </ul>
      </div>
    </div>
  )
}

export default function IncidentInvestigation() {
  const { reportId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const debug = searchParams.get('debug') === '1'
  const rawTab = searchParams.get('tab') || 'summary'
  const tab = rawTab === 'overview' ? 'summary' : rawTab
  const setTab = (next) => {
    const params = new URLSearchParams(searchParams)
    params.set('tab', next)
    setSearchParams(params, { replace: true })
  }

  const tabs = debug ? [...BASE_TABS, { id: 'raw', label: 'Raw' }] : BASE_TABS
  const { data, loading, error, reload } = useFetch(
    `/api/incidents/${encodeURIComponent(reportId)}`,
    [reportId],
  )
  const report = useMemo(() => (data ? normalizeReport(data) : null), [data])
  const graphUrl = tab === 'topology'
    ? `/api/graphs/incident?report_id=${encodeURIComponent(reportId)}`
    : null
  const graph = useFetch(graphUrl, [tab, reportId])
  const [ackBusy, setAckBusy] = useState(false)
  const [topoNode, setTopoNode] = useState(null)

  if (loading && !report) return <Spinner label="Loading investigation" />
  if (error && !report) return <ErrorMsg error={error} />
  if (!report) return <ErrorMsg error={{ message: 'Report not found' }} />

  const anomalyValues = (report.anomaly_score_list || []).map(row => Number(row.score || 0))
  const abstained = isAbstainedSummary(report.root_cause_summary)
  const narrative = buildRcaNarrative(report, { abstained })
  const refs = report.telemetry_refs || {}
  const windowQs = new URLSearchParams()
  if (report.window_start) windowQs.set('from', report.window_start)
  if (report.window_end) windowQs.set('to', report.window_end)
  if (report.alerted_service) windowQs.set('service', report.alerted_service)
  const hasAnomalies = (report.anomaly_score_list || []).length > 0

  const onAck = async () => {
    setAckBusy(true)
    try {
      await apiFetch(`/api/incidents/${encodeURIComponent(reportId)}/ack`, {
        method: 'POST',
        body: { acked_by: 'operator' },
      })
      await reload()
    } finally {
      setAckBusy(false)
    }
  }

  return (
    <div className="watchdog-page investigation-page">
      <header className="investigation-header investigation-header--slim">
        <div>
          <p className="eyebrow">
            <Link to="/incidents">Incidents</Link> / {report.incident_id || report.id}
          </p>
          <h1>{report.alert_name || 'RCA report'}</h1>
          <div className="chip-row">
            <span className={`status-chip status-${report.severity}`}>{report.severity}</span>
            <span className="status-chip">{report.incident_status || '—'}</span>
          </div>
        </div>
      </header>

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

      {tab === 'summary' && (
        <div className="tab-panel investigation-overview">
          <AnswerCard
            report={report}
            narrative={narrative}
            abstained={abstained}
            onAck={onAck}
            ackBusy={ackBusy}
            onRemediate={() => setTab('remediation')}
            onOpenTab={setTab}
          />
          {narrative.impact && (
            <div className="panel">
              <h2>Impact</h2>
              <p>{narrative.impact}</p>
              {!abstained && (report.blast_radius || []).length > 0 && (
                <div className="chip-row abstain-evidence__scope">
                  {(report.blast_radius || []).map(node => (
                    <Link key={node} className="status-chip" to={`/metrics?${serviceQs(windowQs, node)}`}>
                      {node}
                    </Link>
                  ))}
                </div>
              )}
              {(report.amplifier_nodes || []).length > 0 && (
                <p className="muted">Amplifiers: {(report.amplifier_nodes || []).join(', ')}</p>
              )}
            </div>
          )}
          <div className="panel">
            <h2>Feedback</h2>
            <div className="chip-row">
              <button
                type="button"
                className="btn"
                onClick={() => apiFetch(`/api/incidents/${encodeURIComponent(reportId)}/feedback`, {
                  method: 'POST',
                  body: { verdict: 'accepted' },
                }).then(reload)}
              >
                Accept
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => apiFetch(`/api/incidents/${encodeURIComponent(reportId)}/feedback`, {
                  method: 'POST',
                  body: { verdict: 'rejected' },
                }).then(reload)}
              >
                Reject
              </button>
              {report.feedback?.verdict && (
                <span className="status-chip">{report.feedback.verdict}</span>
              )}
            </div>
          </div>
          <details className="fold-panel">
            <summary>Analysis metadata</summary>
            <dl className="meta-grid">
              <div><dt>Report id</dt><dd>{report.id || '—'}</dd></div>
              <div><dt>Trace</dt><dd>{report.trace_id || '—'}</dd></div>
              <div><dt>Window</dt><dd>{report.window_start || '—'} → {report.window_end || '—'}</dd></div>
              <div><dt>Created</dt><dd>{report.created_at || '—'}</dd></div>
              <div><dt>Route</dt><dd>{report.route || report.routing || '—'}</dd></div>
              <div><dt>Layer</dt><dd>{report.layer || report.telemetry_refs?.layer || '—'}</dd></div>
              <div>
                <dt>Runbook</dt>
                <dd>
                  {(report.runbook_url || report.telemetry_refs?.runbook_url) ? (
                    <a href={report.runbook_url || report.telemetry_refs.runbook_url} target="_blank" rel="noreferrer">
                      Open runbook
                    </a>
                  ) : '—'}
                </dd>
              </div>
            </dl>
          </details>
        </div>
      )}

      {tab === 'hypotheses' && (
        <div className="tab-panel">
          <div className="panel">
            <h2>Origin candidates</h2>
            <CandidatesList
              candidates={report.origin_candidates}
              abstained={abstained}
              summary={report.root_cause_summary}
              windowQs={windowQs}
            />
          </div>
          {hasAnomalies && (
            <div className="panel">
              <h2>Anomaly scores</h2>
              {anomalyValues.length >= 2 && <AnomalySparkline values={anomalyValues} />}
              <ul className="compact-list">
                {(report.anomaly_score_list || []).slice(0, 12).map(row => (
                  <li key={row.node}>{row.node}: {Number(row.score).toFixed(3)}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {tab === 'evidence' && (
        <div className="tab-panel">
          {abstained ? (
            <AbstainEvidencePanel report={report} windowQs={windowQs} />
          ) : (
            <div className="panel">
              <h2>Evidence</h2>
              {(report.evidence || []).length === 0 && <p className="muted">No evidence items.</p>}
              <ul className="evidence-list">
                {(report.evidence || []).map((ev, idx) => (
                  <li key={`${ev.node}-${idx}`} className="evidence-block">
                    <div className="evidence-head">
                      <strong>{ev.node || 'node'}</strong>
                      <span>{ev.score != null ? Number(ev.score).toFixed(3) : '—'}</span>
                      {ev.role && <span className="status-chip">{ev.role}</span>}
                    </div>
                    <p>{ev.explanation || '—'}</p>
                    <SignalBars signals={ev.signals} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {tab === 'timeline' && (
        <div className="tab-panel">
          <InvestigationTimeline
            events={report.timeline || []}
            rootCause={report.root_cause_service}
            alertedService={report.alerted_service}
            anomalyScores={report.anomaly_score_list || report.anomaly_scores}
            candidates={report.origin_candidates}
          />
          {abstained && (report.timeline || []).length > 0 && (
            <p className="muted abstain-evidence__hint panel">
              Services with the same onset time are treated as competing origins, which can trigger abstention.
            </p>
          )}
        </div>
      )}

      {tab === 'topology' && (
        <div className="tab-panel topology-with-drawer">
          <div className="panel">
            <div className="section-heading">
              <div>
                <h2>Incident subgraph</h2>
                <p className="provenance-chip">Incident-time snapshot when available</p>
              </div>
            </div>
            {graph.loading && <Spinner label="Loading graph" />}
            {graph.error && <ErrorMsg error={graph.error} />}
            {graph.data && (
              <TopologyGraph
                graph={graph.data}
                height={480}
                onNodeSelect={(node) => setTopoNode(node)}
              />
            )}
          </div>
          {topoNode && (
            <aside className="topo-drawer panel">
              <h3>{topoNode.id || topoNode.label || 'Node'}</h3>
              <p className="muted">{topoNode.role || topoNode.status || '—'}</p>
              <div className="rca-story__pivots">
                <Link to={`/metrics?${serviceQs(windowQs, topoNode.id)}`}>Metrics</Link>
                <Link to={`/logs?${serviceQs(windowQs, topoNode.id)}`}>Logs</Link>
                <Link to={`/traces?${serviceQs(windowQs, topoNode.id)}`}>Traces</Link>
              </div>
              <button type="button" className="btn-link" onClick={() => setTopoNode(null)}>Close</button>
            </aside>
          )}
        </div>
      )}

      {tab === 'telemetry' && (
        <div className="tab-panel">
          <div className="panel">
            <h2>Telemetry coverage</h2>
            <div className="coverage-tiles">
              {COVERAGE_TILES.filter(([key]) => refs[key] != null).map(([key, label]) => (
                <div className="coverage-tile" key={key}>
                  <span>{label}</span>
                  <strong>{String(refs[key])}</strong>
                </div>
              ))}
            </div>
            <div className="telemetry-pivots telemetry-pivots--inline">
              <Link to={`/metrics?${windowQs}`}>Metrics</Link>
              <Link to={`/logs?${windowQs}`}>Logs</Link>
              <Link to={`/traces?${windowQs}`}>Traces</Link>
            </div>
          </div>
        </div>
      )}

      {tab === 'remediation' && <RemediationLiveTab report={report} />}

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
