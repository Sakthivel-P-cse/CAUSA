import React, { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useFetch } from '../hooks/useFetch.js'
import { useDashboard } from '../context/DashboardContext.jsx'
import { StatusDot, Spinner, ErrorMsg } from '../components/Common.jsx'
import HealthSummary from '../components/watchdog/HealthSummary.jsx'
import InsightCard, { incidentPriority, incidentSeverity } from '../components/watchdog/InsightCard.jsx'
import AnomalySparkline from '../components/watchdog/AnomalySparkline.jsx'
import { normalizeIncidentPage } from '../api/incidents.js'
import { plainIncidentSummary } from '../api/rcaNarrative.js'

const PRIORITY_SEVERITIES = new Set(['critical', 'high', 'warning', 'medium'])
const SEVERITY_ORDER = ['critical', 'high', 'warning', 'medium', 'low', 'info']
const ALERT_VISIBLE = 3
const INSIGHT_CAP = 6

function buildQuery(filters) {
  const params = new URLSearchParams()
  if (filters.service) params.set('service', filters.service)
  if (filters.from && filters.to) {
    params.set('start', filters.from)
    params.set('end', filters.to)
  }
  params.set('limit', '25')
  params.set('sort', 'severity_desc')
  return params.toString()
}

function ActivityPanel({ items, perDay }) {
  const counts = useMemo(() => {
    const map = Object.fromEntries(SEVERITY_ORDER.map(s => [s, 0]))
    for (const item of items) {
      const sev = incidentSeverity(item)
      map[sev] = (map[sev] || 0) + 1
    }
    return map
  }, [items])
  const max = Math.max(1, ...Object.values(counts))

  return (
    <div className="panel activity-panel">
      <h2>Incident activity</h2>
      <div className="severity-bars" aria-label="Severity breakdown">
        {SEVERITY_ORDER.filter(sev => counts[sev] > 0 || ['critical', 'warning'].includes(sev)).map(sev => (
          <div className="severity-bar-row" key={sev}>
            <span className={`status-chip status-${sev}`}>{sev}</span>
            <div className="severity-bar-track" aria-hidden="true">
              <div
                className={`severity-bar-fill severity-${sev}`}
                style={{ width: `${(counts[sev] / max) * 100}%` }}
              />
            </div>
            <strong>{counts[sev]}</strong>
          </div>
        ))}
      </div>
      {perDay.length >= 2 && (
        <div className="activity-sparkline">
          <AnomalySparkline values={perDay} label="Incidents per day" />
        </div>
      )}
      <div className="activity-panel__footer">
        <Link className="btn-link" to="/incidents">Open inbox</Link>
      </div>
    </div>
  )
}

export default function Overview() {
  const { filters } = useDashboard()
  const refreshMs = filters.refreshSec ? filters.refreshSec * 1000 : 0
  const qs = buildQuery(filters)

  const alerts = useFetch('/api/alerts?limit=10', [], { refreshMs })
  const services = useFetch('/api/metrics/services', [], { refreshMs })
  const stats = useFetch('/api/stats', [], { refreshMs })
  const incidents = useFetch(`/api/incidents?${qs}`, [qs], { refreshMs })
  const remStats = useFetch('/api/remediation/stats', [], { refreshMs })

  const page = useMemo(() => normalizeIncidentPage(incidents.data), [incidents.data])
  const priorityIncidents = useMemo(
    () => [...page.items]
      .filter(i => PRIORITY_SEVERITIES.has(incidentSeverity(i)))
      .sort((a, b) => incidentPriority(b) - incidentPriority(a)),
    [page.items],
  )
  const insightItems = priorityIncidents.slice(0, INSIGHT_CAP)
  const insightOverflow = priorityIncidents.length > INSIGHT_CAP

  const health = {
    activeAlerts: (alerts.data || []).length,
    recentIncidents: stats.data?.total ?? page.total,
    completeAnalyses: page.items.filter(i => i.has_rca || i.root_cause_service).length,
    needsInvestigation: page.items.filter(i => !i.root_cause_service).length,
    degradedServices: (services.data || []).filter(s => s.health !== 'green').length,
  }

  if (incidents.loading && !incidents.data) return <Spinner label="Loading overview" />
  if (incidents.error && !incidents.data) return <ErrorMsg error={incidents.error} />

  const perDay = (stats.data?.incidents_per_day || []).map((row) => {
    if (Array.isArray(row)) return Number(row[1] || 0)
    return Number(row?.count || 0)
  })
  const serviceList = services.data || []
  const alertList = alerts.data || []
  const visibleAlerts = alertList.slice(0, ALERT_VISIBLE)

  return (
    <div className="watchdog-page overview-page">
      <header className="watchdog-page__header">
        <div>
          <h1>Overview</h1>
          <p>Active incidents and service health from Causa RCA.</p>
        </div>
      </header>

      <section className="alert-banner" aria-label="Active alerts">
        <div className="alert-banner__head">
          <strong>
            {alertList.length > 0
              ? `${alertList.length}+ ongoing alert${alertList.length === 1 ? '' : 's'}`
              : 'No ongoing alerts'}
          </strong>
          <Link className="btn-link" to="/incidents">View all</Link>
        </div>
        {visibleAlerts.length > 0 && (
          <ul className="alert-banner__list">
            {visibleAlerts.map(alert => {
              const id = alert.fingerprint || alert.incident_id
              return (
                <li key={id || `${alert.alert_name}-${alert.service}`}>
                  <Link
                    className="alert-banner__link"
                    to={id ? `/incidents/${encodeURIComponent(id)}` : '/incidents'}
                  >
                    <span className={`status-chip status-${alert.severity}`}>{alert.severity}</span>
                    <div className="alert-banner__body">
                      <strong>{alert.alert_name || 'Alert'}</strong>
                      <span className="alert-banner__meta">
                        {alert.service || 'unknown'}
                        {alert.category ? ` · ${alert.category}` : ''}
                      </span>
                      <p>{plainIncidentSummary({ ...alert, alerted_service: alert.service, root_cause_summary: alert.summary })}</p>
                    </div>
                  </Link>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <HealthSummary
        activeAlerts={health.activeAlerts}
        recentIncidents={health.recentIncidents}
        completeAnalyses={health.completeAnalyses}
        needsInvestigation={health.needsInvestigation}
        degradedServices={health.degradedServices}
      />

      {(remStats.data?.awaiting_review || 0) > 0 && (
        <section className="alert-banner rem-banner" aria-label="Proposed remediations">
          <div className="alert-banner__head">
            <strong>{remStats.data.awaiting_review} proposed fix{remStats.data.awaiting_review === 1 ? '' : 'es'} awaiting review</strong>
            <Link className="btn-link" to="/remediation?status=proposed">Open Remediation</Link>
          </div>
        </section>
      )}

      <section className="overview-section">
        <div className="section-heading">
          <div>
            <h2>Watchdog insights</h2>
            <p>Severity-ranked findings — open investigation or filter the inbox.</p>
          </div>
          {insightOverflow && (
            <Link className="btn-link" to="/incidents">View all</Link>
          )}
        </div>
        <div className="insight-grid insight-grid--fixed">
          {insightItems.length === 0 && <p className="muted">No priority or warning incidents in scope.</p>}
          {insightItems.map(incident => (
            <InsightCard key={incident.id} incident={incident} compact showFilterLink />
          ))}
        </div>
      </section>

      <section className="overview-columns">
        <ActivityPanel items={page.items} perDay={perDay} />

        <div className="panel">
          <h2>Service health</h2>
          {services.error && (
            <div className="live-data-notice">
              Prometheus unreachable. Port-forward <code>svc/prometheus:9090</code> then refresh.
            </div>
          )}
          {!services.error && serviceList.length === 0 && !services.loading && (
            <div className="live-data-notice">
              No live service metrics yet. Forward Prometheus to localhost:9090 after the demo stack is up.
            </div>
          )}
          {serviceList.length > 0 && (
            <div className="service-health-grid">
              {serviceList.slice(0, 8).map(svc => {
                const errPct = Math.min(100, Number(svc.error_rate || 0) * 100)
                return (
                  <div className="service-health-row" key={svc.name}>
                    <StatusDot status={svc.health} />
                    <Link to={`/metrics?service=${encodeURIComponent(svc.name)}`}>{svc.name}</Link>
                    <div className="service-health-bar" title={`${errPct.toFixed(1)}% error rate`} aria-hidden="true">
                      <div className="service-health-bar__fill" style={{ width: `${Math.max(errPct, errPct > 0 ? 4 : 0)}%` }} />
                    </div>
                    <span>{errPct.toFixed(1)}% err</span>
                    <span>{Number(svc.latency_p99_ms || 0).toFixed(0)} ms</span>
                    <span>{Number(svc.request_rate || 0).toFixed(1)} r/s</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
