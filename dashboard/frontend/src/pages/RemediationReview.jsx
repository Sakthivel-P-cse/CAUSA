import React, { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useFetch } from '../hooks/useFetch.js'
import { apiFetch } from '../api/client.js'
import { Spinner, ErrorMsg } from '../components/Common.jsx'

const ACTIVE = new Set(['approved', 'claimed', 'executing', 'observing'])
const PROMPT_MAX = 4096

function solutionFromPlan(plan) {
  const details = plan?.details || {}
  const spec = plan?.plan_spec || details.plan_spec || {}
  const patch = (spec.change && spec.change.patch) || {}
  const sol = details.solution || plan?.solution || {}
  return {
    summary: sol.summary || patch.fix_summary || details.operator_remediation || plan?.summary,
    path: sol.path || patch.path || spec.target?.config_key,
    diff: sol.diff || patch.suggested_diff || details.pr?.diff || details.patch?.diff,
    excerpt: sol.excerpt || patch.source_excerpt,
    rollback: details.rollback,
    configPatch: details.patch,
    aiSummary: sol.ai_summary || details.ai_summary || details.rationale || '',
    recommendation: sol.recommendation || details.fix_recommendation || details.operator_remediation || '',
    steps: Array.isArray(sol.steps) ? sol.steps : [],
    risks: sol.risks || '',
    verify: sol.verify || '',
  }
}

function DecideActions({ canDecide, status, decideBusy, regenBusy, onDecide }) {
  const disabled = decideBusy || regenBusy
  if (!canDecide) return null
  if (status === 'proposed') {
    return (
      <>
        <button type="button" className="btn primary" disabled={disabled} onClick={() => onDecide('approve')}>
          {decideBusy ? 'Working…' : 'Approve now'}
        </button>
        <button type="button" className="btn" disabled={disabled} onClick={() => onDecide('reject')}>
          Reject
        </button>
      </>
    )
  }
  if (status === 'failed' || status === 'rolled_back') {
    return (
      <button type="button" className="btn primary" disabled={disabled} onClick={() => onDecide('approve')}>
        {decideBusy ? 'Working…' : 'Retry (re-approve)'}
      </button>
    )
  }
  return null
}

export default function RemediationReview() {
  const { planId } = useParams()
  const navigate = useNavigate()
  const { data: plan, loading, error, reload } = useFetch(
    planId ? `/api/remediation/plans/${encodeURIComponent(planId)}` : null,
    [planId],
  )
  const parentId = plan?.details?.parent_plan_id || plan?.details?.regen_of_plan_id || null
  const { data: parentPlan } = useFetch(
    parentId ? `/api/remediation/plans/${encodeURIComponent(parentId)}` : null,
    [parentId],
  )
  const [decideBusy, setDecideBusy] = useState(false)
  const [regenBusy, setRegenBusy] = useState(false)
  const [actionError, setActionError] = useState(null)
  const [showRefine, setShowRefine] = useState(false)
  const [manualPrompt, setManualPrompt] = useState('')
  const [promptMode, setPromptMode] = useState('constraints')
  const [regenReason, setRegenReason] = useState('')
  const [successMessage, setSuccessMessage] = useState('')

  useEffect(() => {
    if (!plan || !ACTIVE.has(plan.status)) return undefined
    const t = setInterval(() => { reload() }, 3000)
    return () => clearInterval(t)
  }, [plan, planId, reload])

  useEffect(() => {
    if (!plan) return
    const existing = plan.details?.operator_prompt || ''
    setManualPrompt(existing)
    setPromptMode(plan.details?.prompt_mode || 'constraints')
  }, [plan])

  const decide = async (kind) => {
    setDecideBusy(true)
    setActionError(null)
    try {
      await apiFetch(`/api/remediation/plans/${encodeURIComponent(planId)}/${kind}`, {
        method: 'POST',
        body: {},
      })
      await reload()
      setSuccessMessage(kind === 'reject'
        ? 'Plan rejected and recorded in the audit timeline.'
        : 'Approval recorded. The worker may now claim this plan.')
    } catch (err) {
      setActionError(err?.message || String(err))
    } finally {
      setDecideBusy(false)
    }
  }

  const regenerate = async () => {
    const prompt = manualPrompt.trim()
    if (!prompt) return
    setRegenBusy(true)
    setActionError(null)
    try {
      const res = await apiFetch(`/api/remediation/plans/${encodeURIComponent(planId)}/regenerate`, {
        method: 'POST',
        body: {
          manual_prompt: prompt,
          prompt_mode: promptMode,
          reason: regenReason.trim() || 'operator refine',
        },
      })
      const newId = res?.plan?.id
      if (newId) {
        navigate(`/remediation/${encodeURIComponent(newId)}`)
      } else {
        await reload()
      }
    } catch (err) {
      setActionError(err?.message || String(err))
    } finally {
      setRegenBusy(false)
    }
  }

  if (loading && !plan) return <Spinner label="Loading plan" />
  if (error && !plan) return <ErrorMsg error={error} />
  if (!plan) return <ErrorMsg error={{ message: 'Plan not found' }} />

  const details = plan.details || {}
  const spec = plan.plan_spec || details.plan_spec || {}
  const rationale = details.rationale || plan.summary
  const source = details.nim_prompt_source || details.source || 'rules'
  const canDecide = plan.status === 'proposed' || plan.status === 'failed' || plan.status === 'rolled_back'
  const policy = details.policy || plan.policy
  const showPolicyError =
    policy
    && policy.allowed === false
    && !['succeeded', 'approved', 'claimed', 'executing', 'observing'].includes(plan.status)
  const events = Array.isArray(plan.events) ? plan.events : []
  const sol = solutionFromPlan(plan)
  const parentSol = parentPlan ? solutionFromPlan(parentPlan) : null
  const failReason = plan.apply_result?.error || plan.apply_result?.reason
  const title = [plan.action, plan.candidate_node].filter(Boolean).join(' · ') || 'Remediation plan'
  const promptLen = manualPrompt.length
  const canRegenerate = Boolean(manualPrompt.trim()) && !regenBusy && !decideBusy
  function requestDecision(kind) {
    setSuccessMessage('')
    decide(kind)
  }

  return (
    <div className="watchdog-page">
      <header className="watchdog-page__header">
        <div>
          <p className="eyebrow">
            <Link to="/remediation">Remediation</Link> / {plan.id?.slice(0, 8)}
          </p>
          <h1>{title}</h1>
          <div className="chip-row">
            <span className="status-chip">{plan.fault_class}</span>
            <span className="status-chip">{plan.status}</span>
            <span className="status-chip">{source}</span>
            {plan.risk_tier && <span className="status-chip">risk:{plan.risk_tier}</span>}
            {ACTIVE.has(plan.status) && <span className="status-chip">worker running</span>}
            {plan.decided_by === 'auto' && <span className="status-chip">auto-approved</span>}
            {details.prompt_mode && <span className="status-chip">prompt:{details.prompt_mode}</span>}
            {(details.parent_plan_id || details.regen_of_plan_id) && <span className="status-chip">refined</span>}
          </div>
        </div>
      </header>

      <div className="chip-row remediation-actions remediation-actions--top">
        <DecideActions
          canDecide={canDecide}
          status={plan.status}
          decideBusy={decideBusy}
          regenBusy={regenBusy}
          onDecide={requestDecision}
        />
        <Link className="btn-link" to="/remediation">Back to queue</Link>
      </div>

      {actionError && <ErrorMsg error={actionError} />}
      {successMessage && <div className="ui-success" role="status">{successMessage}</div>}
      {plan.status === 'failed' && failReason && (
        <ErrorMsg error={{ message: `Failed: ${failReason}` }} />
      )}
      {showPolicyError && (
        <ErrorMsg error={{ message: `Policy: ${policy.reason || policy.code}` }} />
      )}

      <div className="panel">
        <h2>Proposed solution</h2>
        <p>{sol.recommendation || sol.summary || details.operator_remediation || '—'}</p>
        {(sol.aiSummary || rationale) && (
          <>
            <h3 style={{ marginTop: 16 }}>AI summary</h3>
            <p className="muted">{sol.aiSummary || rationale}</p>
          </>
        )}
        {sol.recommendation && sol.recommendation !== sol.aiSummary && (
          <>
            <h3 style={{ marginTop: 16 }}>Fix recommendation</h3>
            <p>{sol.recommendation}</p>
          </>
        )}
        {sol.steps.length > 0 && (
          <>
            <h3 style={{ marginTop: 16 }}>Operator steps</h3>
            <ol className="remediation-steps">
              {sol.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </>
        )}
        {(sol.risks || sol.verify) && (
          <dl className="definition-grid" style={{ marginTop: 12 }}>
            {sol.risks ? <div><dt>Risks</dt><dd>{sol.risks}</dd></div> : null}
            {sol.verify ? <div><dt>Verify</dt><dd>{sol.verify}</dd></div> : null}
          </dl>
        )}
        <dl className="definition-grid" style={{ marginTop: 12 }}>
          <div><dt>Service</dt><dd>{plan.candidate_node || '—'}</dd></div>
          <div><dt>File / target</dt><dd><code>{sol.path || (spec.target ? `${spec.target.kind}/${spec.target.name}` : '—')}</code></dd></div>
          <div><dt>Action</dt><dd>{plan.action || '—'}</dd></div>
          <div><dt>Incident</dt><dd>
            {plan.incident_id ? (
              <Link to={`/incidents/${encodeURIComponent(plan.report_id || plan.incident_id)}`}>{plan.incident_id}</Link>
            ) : '—'}
          </dd></div>
        </dl>
        {details.operator_prompt && (
          <>
            <h3 style={{ marginTop: 16 }}>Operator guidance used</h3>
            <pre className="code-block">{details.operator_prompt}</pre>
          </>
        )}
        {sol.diff && (
          <>
            <h3 style={{ marginTop: 16 }}>Code / diff</h3>
            <pre className="code-block">{sol.diff}</pre>
          </>
        )}
        {sol.configPatch && (
          <>
            <h3 style={{ marginTop: 16 }}>Config change</h3>
            <pre className="code-block">{sol.configPatch.diff || JSON.stringify(sol.configPatch, null, 2)}</pre>
          </>
        )}
        {sol.rollback && (
          <>
            <h3 style={{ marginTop: 16 }}>Rollback steps</h3>
            <pre className="code-block">{JSON.stringify(sol.rollback, null, 2)}</pre>
          </>
        )}
        {sol.excerpt && !sol.diff && (
          <>
            <h3 style={{ marginTop: 16 }}>Source excerpt</h3>
            <pre className="code-block">{sol.excerpt}</pre>
          </>
        )}
      </div>

      {parentPlan && (
        <div className="panel">
          <h2>Compare with previous plan</h2>
          <div className="plan-compare">
            <div className="plan-compare__col">
              <h3>Previous</h3>
              <p>{parentSol?.summary || parentPlan.summary || '—'}</p>
              <p className="muted">{parentPlan.details?.rationale || '—'}</p>
              {parentSol?.diff && <pre className="code-block">{parentSol.diff}</pre>}
              <Link to={`/remediation/${encodeURIComponent(parentPlan.id)}`}>
                Open previous {parentPlan.id?.slice(0, 8)}
              </Link>
            </div>
            <div className="plan-compare__col">
              <h3>Current</h3>
              <p>{sol.summary || '—'}</p>
              <p className="muted">{rationale || '—'}</p>
              {sol.diff && <pre className="code-block">{sol.diff}</pre>}
            </div>
          </div>
        </div>
      )}

      <div className="panel">
        <h2>Not convincing?</h2>
        <p>Refine with a manual prompt. System safety rules stay fixed; your text only steers design.</p>
        {!showRefine ? (
          <button type="button" className="btn" style={{ marginTop: 12 }} onClick={() => setShowRefine(true)}>
            Refine with manual prompt
          </button>
        ) : (
          <div className="remediation-form" style={{ marginTop: 12 }}>
            {regenBusy && (
              <div className="remediation-form__loading" role="status">
                <Spinner label="Calling NIM with your guidance…" />
              </div>
            )}
            <label htmlFor="manual-prompt">Operator guidance</label>
            <textarea
              id="manual-prompt"
              rows={5}
              value={manualPrompt}
              onChange={(e) => setManualPrompt(e.target.value)}
              placeholder="e.g. Prefer circuit-breaker + retries. Avoid schema changes. Focus on payment timeout path."
              maxLength={PROMPT_MAX}
              disabled={regenBusy}
            />
            <div className="remediation-form__meta">
              <span className="muted">{promptLen} / {PROMPT_MAX}</span>
              {!manualPrompt.trim() && <span className="muted">Enter guidance to regenerate</span>}
            </div>
            <label htmlFor="prompt-mode">Mode</label>
            <select
              id="prompt-mode"
              value={promptMode}
              onChange={(e) => setPromptMode(e.target.value)}
              disabled={regenBusy}
            >
              <option value="constraints">constraints (default)</option>
              <option value="append">append</option>
              <option value="replace">replace user instruction</option>
            </select>
            <label htmlFor="regen-reason">Reason</label>
            <input
              id="regen-reason"
              value={regenReason}
              onChange={(e) => setRegenReason(e.target.value)}
              placeholder="Why regenerate?"
              maxLength={500}
              disabled={regenBusy}
            />
            <div className="chip-row remediation-actions" style={{ marginTop: 12 }}>
              <button type="button" className="btn primary" disabled={!canRegenerate} onClick={regenerate}>
                {regenBusy ? 'Regenerating…' : 'Regenerate plan'}
              </button>
              <button type="button" className="btn" disabled={regenBusy} onClick={() => setShowRefine(false)}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {plan.apply_result && (
        <div className="panel explore-pivots-panel">
          <h2>Execution result</h2>
          <pre className="code-block">{JSON.stringify(plan.apply_result, null, 2)}</pre>
        </div>
      )}

      <div className="panel">
        <h2>Audit timeline</h2>
        {events.length === 0 && <p className="muted">No events yet.</p>}
        <ul className="evidence-list">
          {events.map((ev) => (
            <li key={ev.id || `${ev.event_type}-${ev.created_at}`} className="evidence-block">
              <div className="evidence-head">
                <strong>{ev.event_type}</strong>
                <span className="muted">{ev.created_at}</span>
              </div>
              {ev.payload && Object.keys(ev.payload).length > 0 && (
                <pre className="code-block">{JSON.stringify(ev.payload, null, 2)}</pre>
              )}
            </li>
          ))}
        </ul>
      </div>

      {canDecide && (
        <div className="chip-row remediation-actions">
          <DecideActions
            canDecide={canDecide}
            status={plan.status}
            decideBusy={decideBusy}
            regenBusy={regenBusy}
            onDecide={requestDecision}
          />
        </div>
      )}
    </div>
  )
}
