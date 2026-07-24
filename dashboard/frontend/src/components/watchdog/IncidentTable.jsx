import React from 'react'
import { Link } from 'react-router-dom'
import { formatFailureType } from '../../api/incidents.js'
import ConfidenceBadge from './ConfidenceBadge.jsx'
import { incidentSeverity } from './InsightCard.jsx'

export default function IncidentTable({ incidents = [] }) {
  if (!incidents.length) return <div className="empty-state">No incidents match the current filters.</div>
  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <caption className="sr-only">Incident investigation results</caption>
        <thead>
          <tr>
            <th scope="col">Detected</th>
            <th scope="col">Finding</th>
            <th scope="col">Root service</th>
            <th scope="col">Failure type</th>
            <th scope="col">Confidence</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map(incident => (
            <tr key={incident.id}>
              <td>
                <time dateTime={incident.triggered_at}>
                  {incident.triggered_at ? new Date(incident.triggered_at).toLocaleString() : '—'}
                </time>
              </td>
              <td>
                <Link to={`/incidents/${incident.id}`}>
                  {incident.alert_name || incident.failure_type || 'Incident'}
                </Link>
                <span className={`status-chip status-${incidentSeverity(incident)}`}>
                  {incidentSeverity(incident)}
                </span>
              </td>
              <td>{incident.root_cause_service || incident.alerted_service || '—'}</td>
              <td>{formatFailureType(incident)}</td>
              <td>
                <ConfidenceBadge score={incident.confidence_score} />
              </td>
              <td>{incident.incident_status || 'open'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
