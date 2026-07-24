import React from 'react'
import { Link } from 'react-router-dom'
import { formatFailureType } from '../../api/incidents.js'
import { plainIncidentSummary } from '../../api/rcaNarrative.js'
import ConfidenceBadge from './ConfidenceBadge.jsx'

const severityRank = { critical: 4, high: 3, warning: 2, medium: 2, low: 1, info: 0 }

export function incidentSeverity(incident) {
  return String(incident.severity || 'info').toLowerCase()
}

export function incidentPriority(incident) {
  const severity = severityRank[incidentSeverity(incident)] ?? 0
  const confidence = Number(incident.confidence_score ?? incident.confidence ?? 0)
  const active = incident.incident_status === 'resolved' ? 0 : 2
  return active + severity * 2 + confidence
}

export default function InsightCard({ incident, compact = false, showFilterLink = false }) {
  const severity = incidentSeverity(incident)
  const confidence = incident.confidence_score ?? incident.confidence
  const service = incident.root_cause_service || incident.alerted_service || 'Unknown service'
  const filterService = incident.alerted_service || incident.root_cause_service
  const failure = formatFailureType(incident)
  const status = incident.incident_status || 'open'
  const inboxParams = new URLSearchParams()
  if (severity) inboxParams.set('severity', severity)
  if (filterService) inboxParams.set('service', filterService)

  return (
    <article className={`insight-card severity-${severity}${compact ? ' compact' : ''}`}>
      <div className="insight-card__eyebrow">
        <span className={`status-chip status-${severity}`}>{severity}</span>
      </div>
      <Link className="insight-card__title" to={`/incidents/${incident.id}`}>
        {incident.alert_name || failure || 'Incident'}
      </Link>
      <p className="insight-card__summary">
        {plainIncidentSummary(incident)}
      </p>
      <div className="insight-card__meta">
        <span>{service}</span>
        <span>{failure}</span>
        <span>{status}</span>
        {confidence != null && <ConfidenceBadge score={confidence} />}
        <time dateTime={incident.triggered_at}>
          {incident.triggered_at ? new Date(incident.triggered_at).toLocaleString() : 'Unknown time'}
        </time>
      </div>
      {showFilterLink && (
        <div className="insight-card__actions">
          <Link to={`/incidents/${incident.id}`}>Investigate</Link>
          <Link to={`/incidents?${inboxParams}`}>Filter inbox</Link>
        </div>
      )}
    </article>
  )
}
