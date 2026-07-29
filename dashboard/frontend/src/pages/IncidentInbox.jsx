import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useFetch } from '../hooks/useFetch.js'
import { useDashboard } from '../context/DashboardContext.jsx'
import { apiFetch } from '../api/client.js'
import { Spinner, ErrorMsg } from '../components/Common.jsx'
import InsightCard, { incidentPriority } from '../components/watchdog/InsightCard.jsx'
import IncidentTable from '../components/watchdog/IncidentTable.jsx'
import FacetPanel from '../components/watchdog/FacetPanel.jsx'
import { normalizeIncidentPage } from '../api/incidents.js'
import { Button, Input, Select } from '../components/ui/index.js'
import { useIncidentStream } from '../hooks/useIncidentStream.js'
import { apiQueryKey } from '../api/queries.js'

function facetsFromSearch(searchParams) {
  const next = {}
  for (const key of ['severity', 'failure_type', 'status', 'service', 'lifecycle']) {
    const value = searchParams.get(key)
    if (value) next[key] = value
  }
  return next
}

export default function IncidentInbox() {
  const { filters } = useDashboard()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [q, setQ] = useState('')
  const searchRef = useRef(null)
  const [sort, setSort] = useState('newest')
  const [minConfidence, setMinConfidence] = useState('')
  const [view, setView] = useState('table')
  const [offset, setOffset] = useState(0)
  const [facetFilters, setFacetFilters] = useState(() => facetsFromSearch(searchParams))
  const [selected, setSelected] = useState(() => new Set())
  const [cursor, setCursor] = useState(0)
  const [busy, setBusy] = useState(false)
  const [now] = useState(() => Date.now())
  const limit = 25
  const streamStatus = useIncidentStream(true)

  const timeWindow = useMemo(() => {
    if (filters.from && filters.to) {
      return { start: filters.from, end: filters.to }
    }
    if (filters.minutes) {
      const endMs = Math.floor(now / 60_000) * 60_000
      const startMs = endMs - Number(filters.minutes) * 60_000
      return {
        start: new Date(startMs).toISOString(),
        end: new Date(endMs + 60_000 - 1).toISOString(),
      }
    }
    return null
  }, [filters.from, filters.to, filters.minutes, now])

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
  const { data, loading, error, reload } = useFetch(`/api/incidents?${query}`, [query])
  const page = useMemo(() => normalizeIncidentPage(data), [data])

  const items = useMemo(() => {
    let list = page.items
    if (facetFilters.lifecycle) {
      list = list.filter(i => String(i.lifecycle || 'active') === facetFilters.lifecycle)
    }
    if (sort === 'priority') list = [...list].sort((a, b) => incidentPriority(b) - incidentPriority(a))
    return list
  }, [page.items, sort, facetFilters.lifecycle])

  useEffect(() => {
    function onKey(e) {
      if (e.target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
        if (e.key === 'Escape') e.target.blur()
        return
      }
      if (e.key === '/') {
        e.preventDefault()
        searchRef.current?.focus()
        return
      }
      if (e.key === 'j') setCursor(c => Math.min(items.length - 1, c + 1))
      if (e.key === 'k') setCursor(c => Math.max(0, c - 1))
      if (e.key === 'Enter' && items[cursor]) {
        window.location.href = `/incidents/${items[cursor].id}`
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [items, cursor])

  function updateFacets(next) {
    setFacetFilters(next)
    setOffset(0)
    const url = new URLSearchParams(searchParams)
    for (const key of ['severity', 'failure_type', 'status', 'service', 'lifecycle']) {
      if (next[key]) url.set(key, next[key])
      else url.delete(key)
    }
    setSearchParams(url, { replace: true })
  }

  function toggle(id) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function bulk(action) {
    setBusy(true)
    try {
      for (const id of selected) {
        if (action === 'ack') {
          await apiFetch(`/api/incidents/${encodeURIComponent(id)}/ack`, { method: 'POST', body: {} })
        } else if (action === 'snooze') {
          await apiFetch(`/api/incidents/${encodeURIComponent(id)}/snooze`, {
            method: 'POST',
            body: { minutes: 60 },
          })
        }
      }
      setSelected(new Set())
      await reload()
    } finally {
      setBusy(false)
    }
  }

  function prefetch(incident) {
    queryClient.prefetchQuery({
      queryKey: apiQueryKey(`/api/incidents/${incident.id}`, [incident.id]),
      queryFn: () => apiFetch(`/api/incidents/${encodeURIComponent(incident.id)}`),
    })
  }

  if (loading && !data) return <Spinner label="Loading incidents" />
  if (error && !data) return <ErrorMsg error={error} />

  const lifecycleFacets = items.reduce((acc, i) => {
    const k = i.lifecycle || 'active'
    acc[k] = (acc[k] || 0) + 1
    return acc
  }, {})

  return (
    <div className="watchdog-page inbox-page">
      <header className="watchdog-page__header">
        <div>
          <h1>Incidents</h1>
          <p>Operational console — j/k move, / search, Enter open · updates: {streamStatus}</p>
        </div>
        <div className="view-toggle" role="group" aria-label="View mode">
          <button type="button" aria-pressed={view === 'table'} onClick={() => setView('table')}>Table</button>
          <button type="button" aria-pressed={view === 'cards'} onClick={() => setView('cards')}>Cards</button>
        </div>
      </header>

      <form className="incident-query" onSubmit={event => { event.preventDefault(); setOffset(0) }}>
        <Input
          ref={searchRef}
          value={q}
          onChange={e => { setQ(e.target.value); setOffset(0) }}
          placeholder="Search incidents"
          aria-label="Search"
        />
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

      {selected.size > 0 && (
        <div className="bulk-bar">
          <span>{selected.size} selected</span>
          <Button disabled={busy} onClick={() => bulk('ack')}>Acknowledge</Button>
          <Button disabled={busy} onClick={() => bulk('snooze')}>Snooze 1h</Button>
          <Button variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
        </div>
      )}

      <div className="incident-layout">
        <FacetPanel
          facets={{
            severity: page.facets.severity || {},
            failure_type: page.facets.failure_type || {},
            status: page.facets.status || {},
            service: page.facets.service || {},
            lifecycle: lifecycleFacets,
          }}
          filters={facetFilters}
          onChange={(name, value) => updateFacets({ ...facetFilters, [name]: value })}
          onClear={() => updateFacets({})}
        />
        <div className="inbox-results">
          {view === 'table' ? (
            <IncidentTable
              incidents={items}
              selected={selected}
              onToggle={toggle}
              onPrefetch={prefetch}
              now={now}
            />
          ) : (
            <div className="insight-grid insight-grid--fixed">
              {items.map(incident => <InsightCard key={incident.id} incident={incident} />)}
            </div>
          )}
          <div className="pagination">
            <Button variant="ghost" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - limit))}>Prev</Button>
            <span>{Math.min(offset + 1, page.total)}–{Math.min(offset + limit, page.total)} of {page.total}</span>
            <Button variant="ghost" disabled={offset + limit >= page.total} onClick={() => setOffset(offset + limit)}>Next</Button>
          </div>
        </div>
      </div>
    </div>
  )
}
