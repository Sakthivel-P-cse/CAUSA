import React, { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useFetch } from '../hooks/useFetch.js'
import { Spinner, ErrorMsg } from '../components/Common.jsx'

export default function RemediationQueue() {
  const [params, setParams] = useSearchParams()
  const status = params.get('status') || ''
  const debug = params.get('debug') === '1'
  const qs = useMemo(() => {
    const p = new URLSearchParams()
    if (status) p.set('status', status)
    p.set('limit', '100')
    return p.toString()
  }, [status])

  const { data, loading, error, reload } = useFetch(`/api/remediation/plans?${qs}`, [qs])
  const stats = useFetch('/api/remediation/stats', [])
  const nim = useFetch(debug ? '/api/remediation/nim/health' : null, [debug])
  const items = data?.items || []

  const setStatus = (next) => {
    const p = new URLSearchParams(params)
    if (next) p.set('status', next)
    else p.delete('status')
    setParams(p, { replace: true })
  }

  if (loading && !data) return <Spinner label="Loading remediation queue" />
  if (error && !data) return <ErrorMsg error={error} />

  return (
    <div className="watchdog-page">
      <header className="watchdog-page__header">
        <div>
          <h1>Remediation</h1>
          <p>Classified faults with proposed fixes awaiting review.</p>
        </div>
        <button type="button" className="btn-link" onClick={() => reload()}>Refresh</button>
      </header>

      <section className="health-summary" aria-label="Remediation stats">
        <div className="health-summary__item">
          <strong>{stats.data?.awaiting_review ?? '—'}</strong>
          <span>Awaiting review</span>
        </div>
        <div className="health-summary__item">
          <strong>{stats.data?.total ?? '—'}</strong>
          <span>Total plans</span>
        </div>
        {debug && (
          <div className="health-summary__item">
            <strong>{nim.data?.key_present ? (nim.data?.enabled ? 'ready' : 'off') : 'no key'}</strong>
            <span>NVIDIA NIM</span>
          </div>
        )}
      </section>

      <div className="chip-row remediation-actions">
        {['', 'proposed', 'succeeded', 'failed', 'rejected', 'rolled_back'].map(s => (
          <button
            key={s || 'all'}
            type="button"
            className={`status-chip${status === s ? ' status-critical' : ''}`}
            onClick={() => setStatus(s)}
          >
            {s || 'all'}
          </button>
        ))}
      </div>

      <div className="panel">
        <h2>Proposed fixes</h2>
        {items.length === 0 && <p className="muted">No remediation plans yet. Open an incident and propose a fix.</p>}
        <ul className="evidence-list">
          {items.map(plan => (
            <li key={plan.id} className="evidence-block">
              <div className="evidence-head">
                <strong>{plan.candidate_node || 'node'}</strong>
                <span className="status-chip">{plan.fault_class || '—'}</span>
                <span className={`status-chip status-${plan.status === 'proposed' ? 'warning' : plan.status === 'succeeded' ? 'info' : 'critical'}`}>{plan.status}</span>
                {(plan.details?.source || plan.source) && (
                  <span className="status-chip">{plan.details?.source || plan.source}</span>
                )}
              </div>
              <p>{plan.details?.rationale || plan.summary || '—'}</p>
              <div className="rca-story__pivots">
                <Link to={`/remediation/${encodeURIComponent(plan.id)}`}>Review</Link>
                {plan.incident_id && (
                  <Link to={`/incidents/${encodeURIComponent(plan.report_id || plan.incident_id)}`}>Incident</Link>
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>

      {debug && (
        <div className="panel explore-pivots-panel">
          <h2>Eval snapshot</h2>
          <p className="muted">
            Fault-class mix from stored plans (rules + NIM). Live chaos accuracy remains on the eval harness.
          </p>
          <ul className="compact-list">
            {Object.entries(stats.data?.by_fault_class || {}).map(([k, v]) => (
              <li key={k}>{k}: {v}</li>
            ))}
            {Object.keys(stats.data?.by_fault_class || {}).length === 0 && (
              <li className="muted">No classifications yet</li>
            )}
          </ul>
        </div>
      )}
    </div>
  )
}
