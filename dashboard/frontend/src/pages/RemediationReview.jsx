import React, { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useFetch } from '../hooks/useFetch.js'
import { apiFetch } from '../api/client.js'
import { Spinner, ErrorMsg } from '../components/Common.jsx'

export default function RemediationReview() {
  const { planId } = useParams()
  const { data: plan, loading, error, reload } = useFetch(
    planId ? `/api/remediation/plans/${encodeURIComponent(planId)}` : null,
    [planId],
  )
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)

  const decide = async (kind) => {
    setBusy(true)
    setActionError(null)
    try {
      await apiFetch(`/api/remediation/plans/${encodeURIComponent(planId)}/${kind}`, {
        method: 'POST',
        body: { decided_by: 'operator' },
      })
      await reload()
    } catch (err) {
      setActionError(err?.message || String(err))
    } finally {
      setBusy(false)
    }
  }

  if (loading && !plan) return <Spinner label="Loading plan" />
  if (error && !plan) return <ErrorMsg error={error} />
  if (!plan) return <ErrorMsg error={{ message: 'Plan not found' }} />

  const details = plan.details || {}
  const rationale = details.rationale || plan.summary
  const remediation = details.operator_remediation || details.nim?.remediation || plan.summary
  const source = details.source || 'rules'
  const canDecide = plan.status === 'proposed' || plan.status === 'failed' || plan.status === 'rolled_back'
  const applyMode = String(plan.apply_result?.mode || details.apply_mode || '').toLowerCase()
  const isSimulated = applyMode.includes('simulat') || applyMode.includes('dry') || details.dry_run === true

  return (
    <div className="watchdog-page">
      <header className="watchdog-page__header">
        <div>
          <p className="eyebrow">
            <Link to="/remediation">Remediation</Link> / {plan.id?.slice(0, 8)}
          </p>
          <h1>{plan.action || 'Proposed fix'}</h1>
          <p>{rationale}</p>
          <div className="chip-row">
            <span className="status-chip">{plan.fault_class}</span>
            <span className="status-chip">{plan.status}</span>
            <span className="status-chip">{source}</span>
            {plan.depth && <span className="status-chip">{plan.depth}</span>}
            {(isSimulated || canDecide) && (
              <span className="status-chip sim-badge">Simulated (demo)</span>
            )}
          </div>
        </div>
      </header>

      {actionError && <ErrorMsg error={actionError} />}

      <div className="overview-columns">
        <div className="panel">
          <h2>Why</h2>
          <p>{rationale}</p>
          <dl className="definition-grid" style={{ marginTop: 12 }}>
            <div><dt>Candidate</dt><dd>{plan.candidate_node || '—'}</dd></div>
            <div><dt>Fault class</dt><dd>{plan.fault_class || '—'}</dd></div>
            <div><dt>Action</dt><dd>{plan.action || '—'}</dd></div>
            <div><dt>Incident</dt><dd>
              {plan.incident_id ? (
                <Link to={`/incidents/${encodeURIComponent(plan.report_id || plan.incident_id)}`}>{plan.incident_id}</Link>
              ) : '—'}
            </dd></div>
          </dl>
        </div>

        <div className="panel">
          <h2>Proposed remediation</h2>
          <p>{remediation}</p>
          {details.nim_patch && (
            <>
              <h3 style={{ marginTop: 16 }}>NIM patch draft</h3>
              <pre className="code-block">{JSON.stringify(details.nim_patch, null, 2)}</pre>
            </>
          )}
          <details style={{ marginTop: 12 }}>
            <summary>Technical plan details</summary>
            <pre className="code-block">{JSON.stringify({ ...plan, details: { ...details, nim: details.nim } }, null, 2)}</pre>
          </details>
        </div>
      </div>

      {plan.apply_result && (
        <div className="panel explore-pivots-panel">
          <h2>Apply result {isSimulated && <span className="status-chip sim-badge">Simulated (demo)</span>}</h2>
          <pre className="code-block">{JSON.stringify(plan.apply_result, null, 2)}</pre>
        </div>
      )}

      <div className="chip-row remediation-actions">
        {canDecide && (
          <>
            <button type="button" className="btn primary" disabled={busy} onClick={() => decide('approve')}>
              {busy ? 'Working…' : 'Approve & apply (simulated)'}
            </button>
            <button type="button" className="btn" disabled={busy} onClick={() => decide('reject')}>
              Reject
            </button>
          </>
        )}
        <Link className="btn-link" to="/remediation">Back to queue</Link>
      </div>

      {Array.isArray(plan.events) && plan.events.length > 0 && (
        <div className="panel" style={{ marginTop: 16 }}>
          <h2>Events</h2>
          <ul className="compact-list">
            {plan.events.map(ev => (
              <li key={ev.id}>{ev.created_at}: {ev.event_type}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
