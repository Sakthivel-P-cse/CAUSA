import React from 'react'
import { Link } from 'react-router-dom'
import { useFetch } from '../../hooks/useFetch.js'
import { formatFailureType, normalizeIncidentPage } from '../../api/incidents.js'
import { incidentSeverity } from './InsightCard.jsx'

export default function RelatedIncidentsStrip({ service, limit = 4 }) {
  const params = new URLSearchParams({ limit: String(limit), sort: 'severity_desc' })
  if (service) params.set('service', service)
  const { data, loading, error } = useFetch(
    service ? `/api/incidents?${params}` : null,
    [service, limit],
  )
  const page = normalizeIncidentPage(data)
  const items = page.items || []

  if (!service || loading || error || items.length === 0) return null

  return (
    <section className="related-incidents-strip" aria-label="Related incidents">
      <div className="related-incidents-strip__head">
        <span>Related incidents for {service}</span>
        <Link className="explorer-link" to={`/incidents?service=${encodeURIComponent(service)}`}>
          Open inbox
        </Link>
      </div>
      <ul className="related-incidents-list">
        {items.map(item => (
          <li key={item.id}>
            <Link to={`/incidents/${encodeURIComponent(item.id)}`}>
              <span className={`status-chip status-${incidentSeverity(item)}`}>{incidentSeverity(item)}</span>
              <strong>{item.alert_name || formatFailureType(item)}</strong>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}
