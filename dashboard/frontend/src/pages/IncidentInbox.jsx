import React, { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useFetch } from '../hooks/useFetch.js'
import { useDashboard } from '../context/DashboardContext.jsx'
import { Spinner, ErrorMsg } from '../components/Common.jsx'
import InsightCard, { incidentPriority } from '../components/watchdog/InsightCard.jsx'
import IncidentTable from '../components/watchdog/IncidentTable.jsx'
import FacetPanel from '../components/watchdog/FacetPanel.jsx'
import { normalizeIncidentPage } from '../api/incidents.js'
import { Button, Input, Select } from '../components/ui/index.js'

function facetsFromSearch(searchParams) {
  const next = {}
  for (const key of ['severity', 'failure_type', 'status', 'service']) {
    const value = searchParams.get(key)
    if (value) next[key] = value
  }
  return next
}

export default function IncidentInbox() {
  const { filters } = useDashboard()
  const [searchParams, setSearchParams] = useSearchParams()
  const [q, setQ] = useState('')
  const [sort, setSort] = useState('newest')
  const [minConfidence, setMinConfidence] = useState('')
  const [view, setView] = useState('table')
  const [offset, setOffset] = useState(0)
  const [facetFilters, setFacetFilters] = useState(() => facetsFromSearch(searchParams))
  const limit = 25

  const timeWindow = useMemo(() => {
    if (filters.from && filters.to) {
      return { start: filters.from, end: filters.to }
    }
    if (filters.minutes) {
      const endMs = Math.floor(Date.now() / 60_000) * 60_000
      const startMs = endMs - Number(filters.minutes) * 60_000
      return {
        start: new Date(startMs).toISOString(),
        end: new Date(endMs + 60_000 - 1).toISOString(),
      }
    }
    return null
  }, [filters.from, filters.to, filters.minutes])

  const params = new URLSearchParams()
  if (q) params.set('q', q)
  if (filters.service) params.set('service', filters.service)
  if (minConfidence) params.set('min_confidence', minConfidence)
  params.set('sort', sort === 'priority' ? 'severity_desc' : sort === 'confidence' ? 'confidence_desc' : 'newest')
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  if (facetFilters.severity) params.set('severity', facetFilters.severity)
  if (facetFilters.failure_type) params.set('failure_type', facetFilters.failure_type)
  if (facetFilters.status) params.set('status', facetFilters.status)
  if (facetFilters.service) params.set('service', facetFilters.service)
  if (timeWindow) {
    params.set('start', timeWindow.start)
    params.set('end', timeWindow.end)
  }

  const query = params.toString()
  const { data, loading, error } = useFetch(`/api/incidents?${query}`, [query])
  const page = useMemo(() => normalizeIncidentPage(data), [data])

  const items = useMemo(() => {
    if (sort !== 'priority') return page.items
    return [...page.items].sort((a, b) => incidentPriority(b) - incidentPriority(a))
  }, [page.items, sort])

  function updateFacets(next) {
    setFacetFilters(next)
    setOffset(0)
    const url = new URLSearchParams(searchParams)
    for (const key of ['severity', 'failure_type', 'status', 'service']) {
      if (next[key]) url.set(key, next[key])
      else url.delete(key)
    }
    setSearchParams(url, { replace: true })
  }

  if (loading && !data) return <Spinner label="Loading incidents" />
  if (error && !data) return <ErrorMsg error={error} />

  return (
    <div className="watchdog-page inbox-page">
      <header className="watchdog-page__header">
        <div>
          <h1>Incidents & RCA</h1>
          <p>Causa incident inbox from Postgres / local RCA dumps.</p>
        </div>
        <div className="view-toggle" role="group" aria-label="View mode">
          <button type="button" aria-pressed={view === 'table'} onClick={() => setView('table')}>Table</button>
          <button type="button" aria-pressed={view === 'cards'} onClick={() => setView('cards')}>Cards</button>
        </div>
      </header>

      <form className="incident-query" onSubmit={event => { event.preventDefault(); setOffset(0) }}>
        <Input value={q} onChange={e => { setQ(e.target.value); setOffset(0) }} placeholder="Search incidents" aria-label="Search" />
        <Select value={sort} onChange={e => setSort(e.target.value)} aria-label="Sort">
          <option value="newest">Newest</option>
          <option value="priority">Highest priority</option>
          <option value="confidence">Highest confidence</option>
        </Select>
        <Select value={minConfidence} onChange={e => { setMinConfidence(e.target.value); setOffset(0) }} aria-label="Minimum confidence">
          <option value="">Any confidence</option>
          <option value="0.25">≥ 25%</option>
          <option value="0.5">≥ 50%</option>
          <option value="0.75">≥ 75%</option>
        </Select>
      </form>

      <p className="result-summary">
        {page.total} incident{page.total === 1 ? '' : 's'}
        {filters.minutes && !filters.from ? ` · last ${filters.minutes}m` : ''}
        {filters.from && filters.to ? ' · absolute window' : ''}
        {facetFilters.severity ? ` · severity: ${facetFilters.severity}` : ''}
        {facetFilters.service ? ` · service: ${facetFilters.service}` : ''}
        {facetFilters.status ? ` · status: ${facetFilters.status}` : ''}
      </p>

      {!loading && page.total === 0 && (
        <p className="empty-hint">
          No incidents in this time window. Widen the top-bar range (e.g. Last 6 hours) or clear the service filter.
        </p>
      )}

      <div className="incident-layout">
        <FacetPanel
          facets={{
            severity: page.facets.severity || {},
            failure_type: page.facets.failure_type || {},
            status: page.facets.status || {},
            service: page.facets.service || {},
          }}
          filters={facetFilters}
          onChange={(name, value) => {
            updateFacets({ ...facetFilters, [name]: value })
          }}
          onClear={() => { updateFacets({}) }}
        />
        <div className="inbox-results">
          {view === 'table' ? (
            <IncidentTable incidents={items} />
          ) : (
            <div className="insight-grid insight-grid--fixed">
              {items.map(incident => <InsightCard key={incident.id} incident={incident} />)}
            </div>
          )}
          <div className="pagination">
            <Button variant="ghost" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</Button>
            <span>{Math.min(offset + 1, page.total)}–{Math.min(offset + limit, page.total)} of {page.total}</span>
            <Button variant="ghost" disabled={offset + limit >= page.total} onClick={() => setOffset(offset + limit)}>Next</Button>
          </div>
        </div>
      </div>
    </div>
  )
}
