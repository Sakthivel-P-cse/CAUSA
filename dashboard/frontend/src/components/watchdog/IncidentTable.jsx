import React from 'react'
import { Link } from 'react-router-dom'
import { formatFailureType, isAbstainedSummary, parseAbstainReason } from '../../api/incidents.js'
import ConfidenceBadge from './ConfidenceBadge.jsx'
import { incidentSeverity } from './InsightCard.jsx'

export default function IncidentTable({
  incidents = [],
  selected = new Set(),
  onToggle,
  onPrefetch,
  now,
}) {
  if (!incidents.length) return <div className="empty-state">No incidents match the current filters.</div>

  function ageLabel(incident) {
    if (!incident.triggered_at) return '—'
    const ms = now - new Date(incident.triggered_at).getTime()
    if (!Number.isFinite(ms) || ms < 0) return '—'
    const m = Math.round(ms / 60000)
    if (m < 60) return `${m}m`
    if (m < 1440) return `${Math.round(m / 60)}h`
    return `${Math.round(m / 1440)}d`
  }

  function ageClass(incident) {
    if (!incident.triggered_at || incident.lifecycle === 'resolved') return 'age-cool'
    const m = (now - new Date(incident.triggered_at).getTime()) / 60000
    if (m < 15 && incidentSeverity(incident) === 'critical') return 'age-hot'
    if (m < 60) return 'age-warm'
    return 'age-cool'
  }

  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <caption className="sr-only">Incident investigation results</caption>
        <thead>
          <tr>
            {onToggle && <th scope="col"><span className="sr-only">Select</span></th>}
            <th scope="col">Service</th>
            <th scope="col">Failure</th>
            <th scope="col">Confidence</th>
            <th scope="col">Lifecycle</th>
            <th scope="col">Age</th>
            <th scope="col">Route</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map(incident => (
            <tr
              key={incident.id}
              onMouseEnter={() => onPrefetch?.(incident)}
              className={selected.has(incident.id) ? 'is-selected' : ''}
            >
              {onToggle && (
                <td>
                  <input
                    type="checkbox"
                    checked={selected.has(incident.id)}
                    onChange={() => onToggle(incident.id)}
                    aria-label={`Select ${incident.alert_name || incident.id}`}
                  />
                </td>
              )}
              <td>
                <span className={`status-dot status-${incidentSeverity(incident)}`} aria-hidden="true" />
                <Link to={`/incidents/${incident.id}`}>
                  {incident.root_cause_service || incident.alerted_service || incident.alert_name || 'Incident'}
                </Link>
              </td>
              <td>{formatFailureType(incident)}</td>
              <td>
                <ConfidenceBadge
                  score={incident.confidence_score}
                  abstained={isAbstainedSummary(incident.root_cause_summary)}
                  abstainReason={parseAbstainReason(incident.root_cause_summary)}
                  compact
                />
              </td>
              <td><span className="status-chip">{incident.lifecycle || incident.incident_status || 'active'}</span></td>
              <td><span className={`age-chip ${ageClass(incident)}`}>{ageLabel(incident)}</span></td>
              <td>{incident.routing || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
