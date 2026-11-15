import React from 'react'
import { Link } from 'react-router-dom'
import AnomalySparkline from './AnomalySparkline.jsx'
import {
  buildAbstainEvidence,
  formatAbstainReason,
  parseAbstainReason,
} from '../../api/incidents.js'

function serviceQs(baseQs, node) {
  const params = new URLSearchParams(baseQs)
  if (node) params.set('service', node)
  return params.toString()
}

function formatTs(value) {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString()
}

function ExploreLinks({ windowQs, service }) {
  const q = serviceQs(windowQs, service)
  return (
    <div className="rca-story__pivots abstain-evidence__links">
      <Link to={`/metrics?${q}`}>Metrics</Link>
      <Link to={`/logs?${q}`}>Logs</Link>
      <Link to={`/traces?${q}`}>Traces</Link>
    </div>
  )
}

function AnomalyTable({ rows, alerted, windowQs }) {
  if (!rows.length) return <p className="muted">No anomaly scores captured.</p>
  return (
    <div className="anomaly-table-wrap">
      <table className="anomaly-table">
        <caption className="sr-only">Anomaly scores by service</caption>
        <thead>
          <tr>
            <th scope="col">Service</th>
            <th scope="col">Anomaly</th>
            <th scope="col">Explore</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const score = Number(row.score) || 0
            const pct = Math.round(score * 100)
            const isAlerted = alerted && row.node === alerted
            return (
              <tr key={row.node} className={isAlerted ? 'is-alerted' : ''}>
                <td>
                  <strong>{row.node}</strong>
                  {isAlerted && <span className="status-chip">alerted</span>}
                </td>
                <td>
                  <div className="anomaly-table__bar" aria-label={`${pct}% anomaly`}>
                    <div className="anomaly-table__fill" style={{ width: `${pct}%` }} />
                    <span>{score.toFixed(3)}</span>
                  </div>
                </td>
                <td><ExploreLinks windowQs={windowQs} service={row.node} /></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function TimelineTable({ events, alerted }) {
  if (!events.length) return <p className="muted">No first-deviation timeline captured.</p>
  const t0 = Date.parse(events[0]?.timestamp || '') || 0
  return (
    <ol className="abstain-timeline">
      {events.map((ev, idx) => {
        const ts = Date.parse(ev.timestamp || '') || 0
        const offset = t0 && ts ? `+${Math.round((ts - t0) / 1000)}s` : '—'
        const isAlerted = alerted && ev.service === alerted
        return (
          <li key={`${ev.service}-${idx}`} className={isAlerted ? 'is-alerted' : ''}>
            <time>{formatTs(ev.timestamp)}</time>
            <div>
              <strong>{ev.service || '—'}</strong>
              <span className="muted">{ev.event || 'event'}</span>
              {isAlerted && <span className="status-chip">alerted</span>}
            </div>
            <em>{offset}</em>
          </li>
        )
      })}
    </ol>
  )
}

const COVERAGE_TILES = [
  ['edge_count', 'Edges'],
  ['topology_edge_count', 'Topology edges'],
  ['span_count', 'Spans'],
  ['log_record_count', 'Logs'],
  ['metric_point_count', 'Metrics'],
  ['infra_node_count', 'Infra nodes'],
]

export default function AbstainEvidencePanel({ report, windowQs, compact = false }) {
  const { anomalies, timeline, scope } = buildAbstainEvidence(report)
  const alerted = report.alerted_service || report.incident_scope?.alerted_service || null
  const reason = formatAbstainReason(
    parseAbstainReason(report.root_cause_summary)
    || report.inference_evidence?.abstain_reason,
  )
  const refs = report.telemetry_refs || {}
  const anomalyValues = anomalies.map(row => Number(row.score) || 0)
  const simultaneous = timeline.length >= 2
    && new Set(timeline.slice(0, Math.min(3, timeline.length)).map(ev => ev.timestamp)).size === 1

  return (
    <div className={`abstain-evidence${compact ? ' abstain-evidence--compact' : ''}`}>
      <section className="panel abstain-evidence__section">
        <h2>What we know</h2>
        <p className="abstain-evidence__lead">{reason}</p>
        <dl className="definition-grid abstain-evidence__facts">
          <div><dt>Alert</dt><dd>{report.alert_name || '—'}</dd></div>
          <div><dt>Alerted service</dt><dd>{alerted || '—'}</dd></div>
          <div><dt>Services in scope</dt><dd>{scope.length || '—'}</dd></div>
          <div><dt>Strong anomalies</dt><dd>{anomalies.filter(row => Number(row.score) >= 0.7).length || '—'}</dd></div>
        </dl>
        {simultaneous && (
          <p className="muted abstain-evidence__hint">
            Multiple services deviated at the same time — this is why RCA abstained instead of picking one origin.
          </p>
        )}
      </section>

      <section className="panel abstain-evidence__section">
        <div className="section-heading">
          <div>
            <h2>Anomaly signals</h2>
            <p className="muted">Services that crossed the anomaly threshold during the incident window.</p>
          </div>
        </div>
        {anomalyValues.length >= 2 && <AnomalySparkline values={anomalyValues} />}
        <AnomalyTable rows={anomalies} alerted={alerted} windowQs={windowQs} />
      </section>

      <section className="panel abstain-evidence__section">
        <h2>Onset timeline</h2>
        <p className="muted">First deviation time per service, earliest first.</p>
        <TimelineTable events={timeline} alerted={alerted} />
      </section>

      {!compact && scope.length > 0 && (
        <section className="panel abstain-evidence__section">
          <h2>Blast radius</h2>
          <div className="chip-row abstain-evidence__scope">
            {scope.map(node => (
              <Link key={node} className="status-chip" to={`/metrics?${serviceQs(windowQs, node)}`}>
                {node}
              </Link>
            ))}
          </div>
        </section>
      )}

      {!compact && COVERAGE_TILES.some(([key]) => refs[key] != null) && (
        <section className="panel abstain-evidence__section">
          <h2>Telemetry used</h2>
          <div className="coverage-tiles">
            {COVERAGE_TILES.filter(([key]) => refs[key] != null).map(([key, label]) => (
              <div className="coverage-tile" key={key}>
                <span>{label}</span>
                <strong>{String(refs[key])}</strong>
              </div>
            ))}
          </div>
          <div className="telemetry-pivots telemetry-pivots--inline">
            <Link to={`/metrics?${windowQs}`}>Open metrics</Link>
            <Link to={`/logs?${windowQs}`}>Open logs</Link>
            <Link to={`/traces?${windowQs}`}>Open traces</Link>
          </div>
        </section>
      )}
    </div>
  )
}
