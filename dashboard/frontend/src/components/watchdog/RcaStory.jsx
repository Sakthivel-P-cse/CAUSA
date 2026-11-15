import React from 'react'
import { Link } from 'react-router-dom'
import { buildRcaNarrative } from '../../api/rcaNarrative.js'

function looksLikeService(node) {
  const value = String(node || '')
  if (!value || value.includes('/') || value.includes('control-plane')) return false
  return !value.startsWith('node-') || value.includes('-')
}

function ExploreLinks({ qs, service }) {
  const params = new URLSearchParams(qs)
  if (service) params.set('service', service)
  const q = params.toString()
  return (
    <div className="rca-story__pivots">
      <Link to={`/metrics?${q}`}>Metrics</Link>
      <Link to={`/logs?${q}`}>Logs</Link>
      <Link to={`/traces?${q}`}>Traces</Link>
    </div>
  )
}

function NodeTextList({ items }) {
  return (
    <ul className="rca-story__impact-nodes">
      {items.map(n => <li key={n}>{n}</li>)}
    </ul>
  )
}

export default function RcaStory({ report, windowQs, abstained, onRemediate }) {
  const candidates = [...(report.origin_candidates || [])].sort(
    (a, b) => Number(b.score || 0) - Number(a.score || 0),
  )
  const top = candidates[0]
  const narrative = buildRcaNarrative(report, { abstained })
  const blast = report.blast_radius || []
  const amplifiers = report.amplifier_nodes || []
  const rootNode = narrative.root
  const alerted = report.alerted_service
  const exploreService = looksLikeService(rootNode) ? rootNode : alerted

  return (
    <div className="rca-story-wrap">
      <section className="rca-story" aria-label="RCA story">
        <article className="rca-story__card">
          <p className="rca-story__step">1 · Root cause</p>
          <h2>Root cause</h2>
          {abstained || !rootNode ? (
            <p className="muted">{narrative.why}</p>
          ) : (
            <>
              <strong className="rca-story__hero">{rootNode}</strong>
              <div className="chip-row">
                {top?.role && <span className="status-chip">{top.role}</span>}
                {top?.node_kind && <span className="status-chip">{top.node_kind}</span>}
              </div>
              <p>{narrative.why}</p>
            </>
          )}
        </article>

        <article className="rca-story__card">
          <p className="rca-story__step">2 · Critical failure</p>
          <h2>Critical failure</h2>
          <strong className="rca-story__hero">{report.alert_name || 'Alert symptom'}</strong>
          <div className="chip-row">
            <span className={`status-chip status-${report.severity}`}>{report.severity}</span>
            <span className="status-chip">{narrative.failureLabel}</span>
          </div>
          <dl className="definition-grid rca-story__defs">
            <div><dt>Alerted service</dt><dd>{alerted || '—'}</dd></div>
            <div><dt>Failure type</dt><dd>{narrative.failureLabel}</dd></div>
          </dl>
        </article>

        <article className="rca-story__card">
          <p className="rca-story__step">3 · Impact</p>
          <h2>Impact</h2>
          {blast.length === 0 && amplifiers.length === 0 ? (
            <p className="muted">{narrative.impact}</p>
          ) : (
            <>
              {blast.length > 0 && (
                <div className="rca-story__impact-block">
                  <span className="rca-story__impact-label">Affected</span>
                  <NodeTextList items={blast} />
                </div>
              )}
              {amplifiers.length > 0 && (
                <div className="rca-story__impact-block">
                  <span className="rca-story__impact-label">Amplifying</span>
                  <NodeTextList items={amplifiers} />
                </div>
              )}
            </>
          )}
        </article>
      </section>
      <div className="rca-story__actions">
        {onRemediate && (
          <button type="button" className="btn primary" onClick={onRemediate}>
            Propose / review fix
          </button>
        )}
        <ExploreLinks qs={windowQs} service={exploreService} />
      </div>
    </div>
  )
}
