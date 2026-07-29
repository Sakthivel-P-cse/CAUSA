import React, { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useFetch } from '../hooks/useFetch.js'
import { Spinner, ErrorMsg, Card, Badge } from '../components/Common.jsx'
import { Button, Input, Select } from '../components/ui/index.js'
import { formatFailureType, isAbstainedSummary, normalizeIncidentPage, normalizeReport } from '../api/incidents.js'

function formatConfidence(item = {}) {
  if (isAbstainedSummary(item.root_cause_summary)) return 'Inconclusive'
  if (item.confidence_score != null) return `${Math.round(item.confidence_score * 100)}%`
  return '—'
}

function HistoryList() {
  const [status, setStatus] = useState('all')
  const [failureType, setFailureType] = useState('')
  const [offset, setOffset] = useState(0)
  const limit = 25
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset), status })
  if (failureType) params.set('failure_type', failureType)

  const query = params.toString()
  const { data, loading, error } = useFetch(`/api/incidents/history?${query}`, [query])
  const page = useMemo(() => normalizeIncidentPage(data), [data])

  if (loading && !data) return <Spinner label="Loading history" />
  if (error && !data) return <ErrorMsg error={error} />

  return (
    <div className="watchdog-page history-page">
      <header className="watchdog-page__header">
        <div>
          <h1>Incident history</h1>
          <p>Durable incident lifecycle from Causa.</p>
        </div>
      </header>
      <form className="incident-query" onSubmit={e => e.preventDefault()}>
        <Select value={status} onChange={e => { setStatus(e.target.value); setOffset(0) }} aria-label="Status">
          <option value="all">All statuses</option>
          <option value="open">Active</option>
          <option value="resolved">Resolved</option>
        </Select>
        <Input
          value={failureType}
          onChange={e => { setFailureType(e.target.value); setOffset(0) }}
          placeholder="Failure type"
          aria-label="Failure type"
        />
      </form>
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Incident</th>
              <th>Status</th>
              <th>Root</th>
              <th>Failure</th>
              <th>Confidence</th>
              <th>First seen</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map(item => (
              <tr key={item.incident_id || item.id}>
                <td>
                  <Link to={`/incidents/history/${encodeURIComponent(item.incident_id || item.id)}`}>
                    {item.incident_id || item.id}
                  </Link>
                </td>
                <td><Badge>{item.incident_status}</Badge></td>
                <td>{item.root_cause_service || item.alerted_service || '—'}</td>
                <td>{formatFailureType(item)}</td>
                <td>{formatConfidence(item)}</td>
                <td>{item.triggered_at ? new Date(item.triggered_at).toLocaleString() : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="pagination">
        <Button variant="ghost" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</Button>
        <span>{Math.min(offset + 1, page.total)}–{Math.min(offset + limit, page.total)} of {page.total}</span>
        <Button variant="ghost" disabled={offset + limit >= page.total} onClick={() => setOffset(offset + limit)}>Next</Button>
      </div>
    </div>
  )
}

function HistoryDetail({ incidentId }) {
  const { data, loading, error } = useFetch(
    `/api/incidents/history/${encodeURIComponent(incidentId)}`,
    [incidentId],
  )
  const item = useMemo(() => (data ? normalizeReport(data) : null), [data])

  if (loading && !item) return <Spinner label="Loading incident" />
  if (error && !item) return <ErrorMsg error={error} />
  if (!item) return <ErrorMsg error={{ message: 'Not found' }} />

  const failureLabel = formatFailureType(item)

  return (
    <div className="watchdog-page history-page">
      <header className="watchdog-page__header">
        <div>
          <p className="eyebrow"><Link to="/incidents/history">History</Link> / {item.incident_id}</p>
          <h1>{item.alert_name || item.incident_id}</h1>
          <div className="chip-row">
            <span className={`status-chip status-${item.severity}`}>{item.severity}</span>
            <span className="status-chip">{item.incident_status}</span>
            <span className="status-chip">{failureLabel}</span>
          </div>
        </div>
      </header>
      <Card title="Durable definition">
        <dl className="definition-grid">
          <div><dt>Alerted service</dt><dd>{item.alerted_service || '—'}</dd></div>
          <div><dt>Root cause</dt><dd>{item.root_cause_service || '—'}</dd></div>
          <div><dt>Failure type</dt><dd>{failureLabel}</dd></div>
          <div><dt>Window</dt><dd>{item.window_start || '—'} → {item.window_end || '—'}</dd></div>
          <div><dt>Resolved</dt><dd>{item.resolved_at || '—'}</dd></div>
        </dl>
      </Card>
      <Card title="RCA report versions">
        {(() => {
          const reports = item.reports || [{ id: item.id, triggered_at: item.triggered_at, root_cause_service: item.root_cause_service, failure_type: item.failure_type, confidence_score: item.confidence_score, root_cause_summary: item.root_cause_summary, remediation: item.remediation }]
          return reports.map((report, idx) => {
            const prev = idx > 0 ? reports[idx - 1] : null
            const rootChanged = prev && prev.root_cause_service !== report.root_cause_service
            const confPrev = prev?.confidence_score
            const confNext = report.confidence_score
            const confChanged = prev && confPrev != null && confNext != null && Math.abs(confPrev - confNext) > 0.01
            return (
              <div key={report.id} className="history-report-row">
                <div>
                  <Link to={`/incidents/${encodeURIComponent(report.id)}`}>{report.id}</Link>
                  <span className="muted"> v{idx + 1}</span>
                  {(rootChanged || confChanged) && (
                    <p className="history-diff">
                      Re-analysis changed
                      {rootChanged ? ` root ${prev.root_cause_service || '—'} → ${report.root_cause_service || '—'}` : ''}
                      {confChanged ? ` · confidence ${Math.round(confPrev * 100)}%→${Math.round(confNext * 100)}%` : ''}
                    </p>
                  )}
                  <p className="muted" style={{ fontSize: 11 }}>Immutable snapshot at incident time</p>
                </div>
                <span>{report.root_cause_service || '—'}</span>
                <span>{formatFailureType(report)}</span>
                <span>{formatConfidence(report)}</span>
                <time>{report.triggered_at ? new Date(report.triggered_at).toLocaleString() : '—'}</time>
              </div>
            )
          })
        })()}
      </Card>
    </div>
  )
}

export default function IncidentHistory() {
  const { incidentId } = useParams()
  if (incidentId) return <HistoryDetail incidentId={incidentId} />
  return <HistoryList />
}
