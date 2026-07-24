import React, { useEffect, useMemo, useState } from 'react'
import { useFetch } from '../hooks/useFetch.js'
import { useExplorerScope } from '../hooks/useExplorerQuery.js'
import {
  ExplorerHeader,
  ExplorerState,
  ExplorerSummary,
  FacetButton,
  formatDuration,
  initialUrlParam,
  setUrlParams,
} from '../components/explorer/Explorer.jsx'

function spanHierarchy(spans) {
  const byId = new Map(spans.map(span => [span.span_id, span]))
  const depthFor = span => {
    let depth = 0
    let current = span
    const visited = new Set([span.span_id])
    while (current.parent_span_id && byId.has(current.parent_span_id) && !visited.has(current.parent_span_id)) {
      visited.add(current.parent_span_id)
      current = byId.get(current.parent_span_id)
      depth += 1
    }
    return depth
  }
  return [...spans].sort((a, b) => a.start_time - b.start_time).map(span => ({ ...span, depth: depthFor(span) }))
}

function criticalPathIds(spans) {
  if (!spans.length) return new Set()
  const byId = new Map(spans.map(span => [span.span_id, span]))
  let current = spans.reduce((latest, span) => (
    span.start_time + span.duration_ms > latest.start_time + latest.duration_ms ? span : latest
  ))
  const result = new Set()
  while (current && !result.has(current.span_id)) {
    result.add(current.span_id)
    current = byId.get(current.parent_span_id)
  }
  return result
}

function SpanBar({ span, minTs, totalMs, critical, selected, onSelect }) {
  const left = totalMs > 0 ? ((span.start_time - minTs) / totalMs) * 100 : 0
  const width = totalMs > 0 ? Math.max((span.duration_ms / totalMs) * 100, 0.5) : 1
  const isError = Number(span.status_code) === 2
  return (
    <button
      type="button"
      className={`waterfall-row${isError ? ' is-error' : ''}${critical ? ' is-critical' : ''}${selected ? ' is-selected' : ''}`}
      onClick={() => onSelect(span)}
      aria-label={`${span.service}, ${span.operation}, ${formatDuration(span.duration_ms)}${isError ? ', error' : ''}${critical ? ', critical path' : ''}`}
    >
      <span className="waterfall-label" style={{ paddingLeft: Math.min(span.depth, 8) * 14 }} title={`${span.service} · ${span.operation}`}>
        {span.depth > 0 ? '↳ ' : ''}<strong>{span.service}</strong> · {span.operation}
      </span>
      <span className="waterfall-track">
        <span className="waterfall-bar" style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }} />
      </span>
      <span className="waterfall-duration">{formatDuration(span.duration_ms)}</span>
    </button>
  )
}

function TraceDetail({ traceId }) {
  const { data, loading, error } = useFetch(traceId ? `/api/traces/${traceId}` : null, [traceId])
  const [selectedSpan, setSelectedSpan] = useState(null)
  const spans = useMemo(() => spanHierarchy(data?.spans ?? []), [data])
  const minTs = spans.length ? Math.min(...spans.map(span => span.start_time)) : 0
  const maxEnd = spans.length ? Math.max(...spans.map(span => span.start_time + span.duration_ms)) : 0
  const total = Math.max(0, maxEnd - minTs)
  const critical = useMemo(() => criticalPathIds(spans), [spans])
  const errors = spans.filter(span => Number(span.status_code) === 2)
  const services = new Set(spans.map(span => span.service)).size

  return (
    <ExplorerState loading={loading} error={error} empty={!spans.length} emptyTitle="No spans returned">
      <div>
        <div className="explorer-stat-grid">
          <div className="explorer-stat"><span>End-to-end duration</span><strong>{formatDuration(total)}</strong></div>
          <div className="explorer-stat"><span>Spans</span><strong>{spans.length}</strong></div>
          <div className="explorer-stat"><span>Services</span><strong>{services}</strong></div>
          <div className="explorer-stat"><span>Error spans</span><strong>{errors.length}</strong></div>
        </div>
        <div className="explorer-section-heading">
          <div><h2>Span waterfall</h2><p>Hierarchy follows parent span IDs. Amber marks the latest-ending parent chain.</p></div>
          <div className="explorer-actions"><span className="explorer-chip">critical path</span><span className="explorer-chip">error</span></div>
        </div>
        <div className="waterfall-scroll">
          <div className="waterfall" role="list" aria-label="Trace spans">
            {spans.map(span => (
              <SpanBar
                key={span.span_id}
                span={span}
                minTs={minTs}
                totalMs={total}
                critical={critical.has(span.span_id)}
                selected={selectedSpan?.span_id === span.span_id}
                onSelect={setSelectedSpan}
              />
            ))}
          </div>
        </div>
        {selectedSpan && (
          <section className="span-inspector" aria-label="Span inspector">
            <div><span>Service</span><strong>{selectedSpan.service}</strong></div>
            <div><span>Operation</span><strong>{selectedSpan.operation}</strong></div>
            <div><span>Duration</span><strong>{formatDuration(selectedSpan.duration_ms)}</strong></div>
            <div><span>Status</span><strong>{Number(selectedSpan.status_code) === 2 ? 'Error' : 'OK'}</strong></div>
            <div><span>Span ID</span><strong className="mono">{selectedSpan.span_id}</strong></div>
            <div><span>Parent ID</span><strong className="mono">{selectedSpan.parent_span_id || 'root'}</strong></div>
            {selectedSpan.error_message && <div style={{ gridColumn: '1 / -1' }}><span>Error message</span><strong>{selectedSpan.error_message}</strong></div>}
          </section>
        )}
      </div>
    </ExplorerState>
  )
}

export default function Traces() {
  const { filters, refreshMs } = useExplorerScope()

  const urlService = initialUrlParam('service')
  const [errorOnly, setErrorOnly] = useState(() => initialUrlParam('errors') === 'true')
  const [query, setQuery] = useState(() => ({ service: filters.service || urlService, error_only: initialUrlParam('errors') === 'true', minutes: filters.minutes }))
  const [selected, setSelected] = useState(() => initialUrlParam('trace'))

  useEffect(() => {
    setQuery(prev => ({ ...prev, service: filters.service || urlService, minutes: filters.minutes }))
  }, [filters.service, filters.minutes])

  const params = new URLSearchParams()
  if (query.service)    params.set('service',    query.service)
  if (query.error_only) params.set('error_only', 'true')
  params.set('minutes', query.minutes)
  params.set('limit', 50)

  const { data: traces, loading, error } = useFetch(
    `/api/traces?${params}`,
    [JSON.stringify(query)],
    { refreshMs }
  )

  function submit(e) {
    e.preventDefault()
    const service = filters.service || urlService
    setQuery({ service, error_only: errorOnly, minutes: filters.minutes })
    setUrlParams({ service, errors: errorOnly })
  }

  function selectTrace(traceId) {
    setSelected(traceId)
    setUrlParams({ trace: traceId })
  }

  return (
    <div className="explorer-page">
      <ExplorerHeader title="Traces" description="Select a recent trace to inspect its service hierarchy, timing, critical chain, and errors." />
      <form className="explorer-toolbar" onSubmit={submit}>
        <FacetButton active={errorOnly} onClick={() => setErrorOnly(value => !value)}>Errors only</FacetButton>
        <span className="explorer-chip">service: {query.service || 'all'}</span>
        <span className="explorer-chip">window: {filters.minutes}m</span>
        <button className="explorer-button explorer-button--primary" type="submit">Run query</button>
      </form>
      <ExplorerSummary facets={[`service: ${query.service || 'all'}`, query.error_only ? 'status: errors' : 'status: all', `window: ${query.minutes}m`, selected && `selected: ${selected.slice(0, 12)}…`]} count={traces?.length || 0} noun="traces" loading={loading} live={refreshMs > 0} />
      <div className="explorer-grid explorer-grid--sidebar">
        <aside className="explorer-card">
          <div className="explorer-section-heading"><div><h2>Recent traces</h2><p>Select a trace ID</p></div></div>
          <ExplorerState loading={loading} error={error} empty={!traces?.length} emptyTitle="No matching traces" emptyBody="Tempo returned no trace IDs. Port-forward tempo:3200 and ensure the OTel Demo is generating traffic.">
            <ul className="explorer-list">
              {traces?.map(traceId => (
                <li key={traceId}>
                  <button type="button" className={`explorer-row-button mono${selected === traceId ? ' is-active' : ''}`} aria-pressed={selected === traceId} onClick={() => selectTrace(traceId)} title={traceId}>
                    {traceId.slice(0, 22)}{traceId.length > 22 ? '…' : ''}
                  </button>
                </li>
              ))}
            </ul>
          </ExplorerState>
        </aside>
        <section className="explorer-card">
          {!selected
            ? <ExplorerState empty emptyTitle="Select a trace" emptyBody="Choose a trace ID to load its span waterfall." />
            : <TraceDetail traceId={selected} />}
        </section>
      </div>
    </div>
  )
}
