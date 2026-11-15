import React, { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useFetch } from '../hooks/useFetch.js'
import { Spinner, ErrorMsg } from '../components/Common.jsx'

function parentPlanId(plan) {
  return plan?.details?.parent_plan_id || plan?.details?.regen_of_plan_id || null
}

function sortByNewest(a, b) {
  const ta = Date.parse(a.created_at || '') || 0
  const tb = Date.parse(b.created_at || '') || 0
  return tb - ta
}

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
  const items = useMemo(
    () => [...(data?.items || [])].sort(sortByNewest),
    [data?.items],
  )

  const setStatus = (next) => {
    const p = new URLSearchParams(params)
    if (next) p.set('status', next)
    else p.delete('status')
    setParams(p, { replace: true })
  }

  if (loading && !data) return <Spinner label="Loading remediation queue" />
  if (error && !data) return <ErrorMsg error={error} />

  const queueItems = items.filter(p => !status || p.status === status)
  const historyItems = items.filter(p => ['succeeded', 'failed', 'approved', 'claimed', 'executing', 'observing', 'rolled_back', 'rejected', 'expired'].includes(p.status))

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
        {['', 'proposed', 'approved', 'executing', 'observing', 'succeeded', 'failed', 'rejected', 'rolled_back'].map(s => (
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
        <h2>Remediation queue</h2>
        {queueItems.length === 0 && <p className="muted">No remediation plans yet — they appear automatically when RCA finishes.</p>}
        <ul className="evidence-list">
          {queueItems.map(plan => {
            const parentId = parentPlanId(plan)
            const promptSource = plan.details?.nim_prompt_source
            const promptMode = plan.details?.prompt_mode
            return (
              <li key={plan.id} className="evidence-block">
                <div className="evidence-head">
                  <strong>{plan.candidate_node || 'node'}</strong>
                  <span className="status-chip">{plan.fault_class || '—'}</span>
                  <span className={`status-chip status-${plan.status === 'proposed' ? 'warning' : plan.status === 'succeeded' ? 'info' : 'critical'}`}>{plan.status}</span>
                  {plan.risk_tier && <span className="status-chip">{plan.risk_tier}</span>}
                  {parentId && <span className="status-chip">refined</span>}
                  {promptSource && <span className="status-chip">{promptSource}</span>}
                  {promptMode && <span className="status-chip">prompt:{promptMode}</span>}
                </div>
                <p>{plan.details?.rationale || plan.summary || '—'}</p>
                <p className="muted">
                  Gate: human approval → remediation worker
                  {plan.plan_spec?.target ? ` · ${plan.plan_spec.target.kind}/${plan.plan_spec.target.name}` : ''}
                  {plan.details?.policy?.allowed === false
                    && !['succeeded', 'approved', 'claimed', 'executing', 'observing'].includes(plan.status)
                    ? ` · policy: ${plan.details.policy.reason}`
                    : ''}
                  {parentId ? (
                    <>
                      {' · '}
                      from{' '}
                      <Link to={`/remediation/${encodeURIComponent(parentId)}`}>{String(parentId).slice(0, 8)}</Link>
                    </>
                  ) : null}
                </p>
                <div className="rca-story__pivots">
                  <Link to={`/remediation/${encodeURIComponent(plan.id)}`}>Review typed plan</Link>
                  {plan.incident_id && (
                    <Link to={`/incidents/${encodeURIComponent(plan.report_id || plan.incident_id)}`}>Originating RCA</Link>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      </div>

      <div className="panel">
        <h2>In-flight / history</h2>
        <ul className="evidence-list">
          {historyItems.map(plan => {
            const parentId = parentPlanId(plan)
            return (
              <li key={`h-${plan.id}`} className="evidence-block">
                <div className="evidence-head">
                  <strong>{plan.candidate_node || plan.id}</strong>
                  <span className="status-chip">{plan.status}</span>
                  {parentId && <span className="status-chip">refined</span>}
                  <span className="muted">{plan.updated_at || plan.created_at || '—'}</span>
                </div>
                <p className="muted">
                  Audit: {plan.decided_by || plan.details?.decided_by || '—'}
                  {plan.claimed_by ? ` · worker ${plan.claimed_by}` : ''}
                  {parentId ? (
                    <>
                      {' · '}
                      from{' '}
                      <Link to={`/remediation/${encodeURIComponent(parentId)}`}>{String(parentId).slice(0, 8)}</Link>
                    </>
                  ) : null}
                </p>
                <Link to={`/remediation/${encodeURIComponent(plan.id)}`}>Open run</Link>
              </li>
            )
          })}
          {historyItems.length === 0 && (
            <li className="muted">No completed or in-flight runs yet.</li>
          )}
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
