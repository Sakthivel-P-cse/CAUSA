import React, { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useFetch } from '../hooks/useFetch.js'
import { useDashboard } from '../context/DashboardContext.jsx'
import { Spinner, ErrorMsg } from '../components/Common.jsx'
import InsightCard, { incidentPriority, incidentSeverity } from '../components/watchdog/InsightCard.jsx'
import { normalizeIncidentPage } from '../api/incidents.js'
import { useIncidentStream } from '../hooks/useIncidentStream.js'

const INSIGHT_CAP = 6

function buildQuery(filters) {
  const params = new URLSearchParams()
  if (filters.service) params.set('service', filters.service)
  if (filters.from && filters.to) {
    params.set('start', filters.from)
    params.set('end', filters.to)
  }
  params.set('limit', '50')
  params.set('sort', 'severity_desc')
  return params.toString()
}

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return '—'
  const s = Math.max(0, Number(seconds))
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  return `${(s / 3600).toFixed(1)}h`
}

function SystemStatusBar({ critical, degraded, active }) {
  let tone = 'good'
  let label = 'All systems operational'
  if (critical > 0) {
    tone = 'danger'
    label = `${critical} critical incident${critical === 1 ? '' : 's'}`
  } else if (degraded > 0 || active > 0) {
    tone = 'warn'
    label = degraded > 0
      ? `${degraded} service${degraded === 1 ? '' : 's'} degraded`
      : `${active} active incident${active === 1 ? '' : 's'}`
  }
  return (
    <div className={`system-status-bar system-status-bar--${tone}`} role="status">
      <span className="system-status-bar__dot" aria-hidden="true" />
      <strong>{label}</strong>
    </div>
  )
}

function servicesFromIncidents(incidents) {
  const seen = new Map()
  for (const inc of incidents) {
    for (const name of [inc.alerted_service, inc.root_cause_service]) {
      if (!name || seen.has(name)) continue
      const sev = incidentSeverity(inc)
      seen.set(name, {
        name,
        health: sev === 'critical' ? 'red' : sev === 'warning' ? 'yellow' : 'green',
        error_rate: 0,
        latency_p99_ms: 0,
        request_rate: 0,
        degraded: true,
      })
    }
  }
  return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name))
}

function ServiceHeatGrid({ services, degraded = false }) {
  const list = Array.isArray(services) ? services : []
  if (!list.length) {
    return <p className="muted empty-hint">No service metrics yet.</p>
  }
  return (
    <>
      {degraded && (
        <p className="live-data-notice live-data-notice--inline">
          Prometheus metrics unavailable — showing services from active incidents.
        </p>
      )}
      <div className="service-heat-grid" aria-label="Service health heat grid">
        {list.map(svc => {
          const errPct = Math.min(100, Number(svc.error_rate || 0) * 100)
          const health = svc.health || (errPct > 5 ? 'red' : errPct > 1 ? 'yellow' : 'green')
          const title = svc.degraded
            ? `${svc.name}: incident signal (metrics unavailable)`
            : `${svc.name}: ${errPct.toFixed(1)}% err · ${Number(svc.latency_p99_ms || 0).toFixed(0)}ms p99 · ${Number(svc.request_rate || 0).toFixed(1)} r/s`
          return (
            <Link
              key={svc.name}
              to={`/metrics?service=${encodeURIComponent(svc.name)}`}
              className={`heat-cell heat-cell--${health}`}
              title={title}
            >
              <span className="heat-cell__name">{svc.name}</span>
            </Link>
          )
        })}
      </div>
    </>
  )
}

function isActiveLifecycle(incident) {
  const life = String(incident.lifecycle || '').toLowerCase()
  if (life === 'resolved') return false
  if (incident.resolved_at) return false
  if (String(incident.incident_status || '').toLowerCase() === 'resolved') return false
  return true
}

export default function Overview() {
  const { filters } = useDashboard()
  const refreshMs = filters.refreshSec ? filters.refreshSec * 1000 : 0
  const qs = buildQuery(filters)
  useIncidentStream(true)

  const alerts = useFetch('/api/alerts?limit=10', [], { refreshMs })
  const services = useFetch('/api/metrics/services', [], { refreshMs })
  const stats = useFetch('/api/stats', [], { refreshMs })
  const incidents = useFetch(`/api/incidents?${qs}`, [qs], { refreshMs })
  const remStats = useFetch('/api/remediation/stats', [], { refreshMs })

  const page = useMemo(() => normalizeIncidentPage(incidents.data), [incidents.data])
  const activeItems = useMemo(
    () => [...page.items]
      .filter(isActiveLifecycle)
      .sort((a, b) => incidentPriority(b) - incidentPriority(a)),
    [page.items],
  )
  const incidentServices = useMemo(
    () => servicesFromIncidents(activeItems),
    [activeItems],
  )
  const insightItems = activeItems.slice(0, INSIGHT_CAP)

  if (incidents.loading && !incidents.data) return <Spinner label="Loading overview" />
  if (incidents.error && !incidents.data) {
    return (
      <div className="watchdog-page overview-page">
        <ErrorMsg error={incidents.error} />
        <p className="muted empty-hint">
          Dashboard API unreachable. Ensure it is running on :8090
          (<code>./causa-startup.sh</code>) then reload.
        </p>
      </div>
    )
  }

  const serviceList = Array.isArray(services.data) ? services.data : []
  const heatServices = serviceList.length ? serviceList : incidentServices
  const heatDegraded = !serviceList.length && incidentServices.length > 0
  const alertList = alerts.data || []
  const degraded = heatServices.filter(s => s.health && s.health !== 'green').length
  const critical = activeItems.filter(i => incidentSeverity(i) === 'critical').length
  const remRate = stats.data?.auto_remediation_rate
  const awaiting = remStats.data?.awaiting_review || 0

  return (
    <div className="watchdog-page overview-page overview-page--glance">
      <SystemStatusBar critical={critical} degraded={degraded} active={activeItems.length} />

      <section className="kpi-strip" aria-label="Operational KPIs">
        <div className="kpi-tile">
          <span>Active</span>
          <strong>{stats.data?.active_incidents ?? activeItems.length}</strong>
        </div>
        <div className="kpi-tile">
          <span>MTTA</span>
          <strong>{formatDuration(stats.data?.mtta_seconds)}</strong>
        </div>
        <div className="kpi-tile">
          <span>MTTR</span>
          <strong>{formatDuration(stats.data?.mttr_seconds)}</strong>
        </div>
        <div className="kpi-tile">
          <span>Auto-rem rate</span>
          <strong>{remRate == null ? '—' : `${Math.round(remRate * 100)}%`}</strong>
        </div>
        <div className="kpi-tile">
          <span>Alerts</span>
          <strong>{alertList.length}</strong>
        </div>
      </section>

      {awaiting > 0 && (
        <div className="glance-rem-link">
          <Link to="/remediation?status=proposed">{awaiting} remediation awaiting review →</Link>
        </div>
      )}

      {insightItems.length > 0 && (
        <section className="overview-section">
          <div className="section-heading">
            <div>
              <h2>Active incidents</h2>
              <p>Needs action now</p>
            </div>
            <Link className="btn-link" to="/incidents">Inbox</Link>
          </div>
          <div className="insight-grid insight-grid--fixed">
            {insightItems.map(incident => (
              <InsightCard key={incident.id} incident={incident} compact showFilterLink />
            ))}
          </div>
        </section>
      )}

      <section className="overview-section">
        <div className="section-heading">
          <div>
            <h2>Services</h2>
            <p>Health heat map — click to open Metrics</p>
          </div>
        </div>
        {services.error && !heatDegraded && (
          <div className="live-data-notice">
            Prometheus unreachable — start port-forward:{' '}
            <code>kubectl -n prometheus port-forward svc/prometheus 9090:9090</code>
          </div>
        )}
        <ServiceHeatGrid services={heatServices} degraded={heatDegraded} />
      </section>
    </div>
  )
}
