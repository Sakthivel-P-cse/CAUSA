import React, { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useFetch } from '../hooks/useFetch.js'
import { useExplorerScope } from '../hooks/useExplorerQuery.js'
import {
  ExplorerField,
  ExplorerHeader,
  ExplorerState,
  ExplorerSummary,
  FacetButton,
  initialUrlParam,
  setUrlParams,
} from '../components/explorer/Explorer.jsx'
import RelatedIncidentsStrip from '../components/watchdog/RelatedIncidentsStrip.jsx'

const LEVEL_COLOUR = { error: 'var(--danger)', warn: 'var(--warn)', info: 'var(--good)', debug: 'var(--muted-2)' }

function formatLogTime(ts) {
  if (ts == null) return ''

  let date
  if (typeof ts === 'number') {
    const ms = ts > 1e15 ? ts / 1_000_000 : (ts > 1e12 ? ts : ts * 1000)
    date = new Date(ms)
  } else if (typeof ts === 'string' && /^\d+$/.test(ts.trim())) {
    const n = Number(ts)
    const ms = n > 1e15 ? n / 1_000_000 : (n > 1e12 ? n : n * 1000)
    date = new Date(ms)
  } else {
    date = new Date(ts)
  }

  if (Number.isNaN(date.getTime())) return String(ts)
  return date.toISOString().slice(11, 23)
}

function logLabels(log) {
  return log.labels && typeof log.labels === 'object' ? log.labels : {}
}

function logSource(log) {
  const labels = logLabels(log)
  return labels.source
    || labels.service_name
    || labels.k8s_deployment_name
    || labels.k8s_container_name
    || labels.container
    || labels.k8s_pod_name
    || labels.pod
    || labels.k8s_namespace_name
    || labels.namespace
    || log.service
    || 'unknown'
}

function traceIdFor(log) {
  const entry = Object.entries(logLabels(log)).find(([key]) => key.toLowerCase().replaceAll(/[^a-z]/g, '') === 'traceid')
  return entry?.[1] || log.trace_id || null
}

function LogRow({ log, index }) {
  const [showRaw, setShowRaw] = useState(false)
  const level = log.level?.toLowerCase() || 'unknown'
  const traceId = traceIdFor(log)
  const labels = logLabels(log)
  const details = { ...log, labels }
  return (
    <details className="log-row">
      <summary aria-label={`Log ${index + 1}: ${log.message || 'no message'}`}>
        <time style={{ color: 'var(--muted-2)' }}>{formatLogTime(log.timestamp)}</time>
        <strong style={{ color: LEVEL_COLOUR[level] || 'var(--muted)', textTransform: 'uppercase' }}>{level}</strong>
        <span style={{ color: 'var(--accent-2)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{logSource(log)}</span>
        <span className="log-row__message">{log.message || '(no message)'}</span>
      </summary>
      <div className="log-row__detail">
        <dl className="log-fields">
          <div><dt>Level</dt><dd>{level}</dd></div>
          <div><dt>Service</dt><dd>{logSource(log)}</dd></div>
          <div><dt>Message</dt><dd>{log.message || '—'}</dd></div>
          {traceId && (
            <div>
              <dt>Trace</dt>
              <dd><Link className="explorer-link" to={`/traces?trace=${encodeURIComponent(traceId)}`}>{String(traceId).slice(0, 24)}…</Link></dd>
            </div>
          )}
        </dl>
        <button type="button" className="btn-link" onClick={() => setShowRaw(v => !v)}>{showRaw ? 'Hide raw' : 'Show raw JSON'}</button>
        {showRaw && <pre>{JSON.stringify(details, null, 2)}</pre>}
      </div>
    </details>
  )
}

function LogHistogram({ logs, onBrush }) {
  const buckets = useMemo(() => {
    const times = logs.map(log => new Date(typeof log.timestamp === 'number' && log.timestamp < 1e12 ? log.timestamp * 1000 : log.timestamp).getTime()).filter(Number.isFinite)
    if (!times.length) return []
    const min = Math.min(...times)
    const max = Math.max(...times)
    const width = Math.max((max - min) / 24, 1)
    const result = Array.from({ length: 24 }, (_, index) => ({ start: min + index * width, end: min + (index + 1) * width, count: 0 }))
    times.forEach(time => { result[Math.min(23, Math.floor((time - min) / width))].count += 1 })
    return result
  }, [logs])
  const highest = Math.max(1, ...buckets.map(bucket => bucket.count))
  return (
    <div>
      <div className="log-histogram" aria-label="Log volume histogram">
        {buckets.map((bucket, index) => (
          <button
            type="button"
            className="log-histogram__bar"
            key={index}
            style={{ height: `${Math.max(3, (bucket.count / highest) * 100)}%` }}
            title={`${bucket.count} logs · click to zoom`}
            onClick={() => onBrush?.(bucket.start, bucket.end)}
          />
        ))}
      </div>
      <p style={{ color: 'var(--muted-2)', fontSize: 10, margin: '8px 4px 12px' }}>Click a bar to zoom the time window</p>
    </div>
  )
}

export default function Logs() {
  const { filters, refreshMs, setFilter } = useExplorerScope()

  const urlService = initialUrlParam('service')
  const [level, setLevel] = useState(() => initialUrlParam('level'))
  const [source, setSource] = useState(() => initialUrlParam('source'))
  const [search, setSearch] = useState(() => initialUrlParam('search'))
  const [liveTail, setLiveTail] = useState(false)
  const [query, setQuery] = useState(() => ({
    service: filters.service || urlService,
    level: initialUrlParam('level'),
    search: initialUrlParam('search'),
    minutes: filters.minutes,
  }))

  useEffect(() => {
    setQuery(prev => ({ ...prev, service: filters.service || urlService, minutes: filters.minutes }))
  }, [filters.service, filters.minutes, urlService])

  const effectiveRefresh = liveTail ? Math.max(refreshMs || 0, 5000) : refreshMs

  const params = new URLSearchParams()
  if (query.service) params.set('service', query.service)
  if (query.level)   params.set('level',   query.level)
  if (query.search)  params.set('search',  query.search)
  if (filters.from && filters.to) {
    params.set('start', filters.from)
    params.set('end', filters.to)
  } else {
    params.set('minutes', query.minutes)
  }
  params.set('limit', 200)

  const { data: logsRaw, loading, error } = useFetch(
    `/api/logs?${params}`,
    [JSON.stringify(query), filters.from, filters.to],
    { refreshMs: effectiveRefresh }
  )

  function submit(e) {
    e.preventDefault()
    const service = filters.service || urlService
    setQuery({ service, level, search, minutes: filters.minutes })
    setUrlParams({ service, level, source, search })
  }

  function brushZoom(startMs, endMs) {
    if (!setFilter) return
    setFilter('from', new Date(startMs).toISOString())
    setFilter('to', new Date(endMs).toISOString())
  }

  const logs = useMemo(() => logsRaw ?? [], [logsRaw])
  const sources = useMemo(() => Array.from(new Set(logs.map(logSource))).sort(), [logs])
  const sourceCounts = useMemo(() => logs.reduce((counts, log) => {
    const value = logSource(log)
    counts[value] = (counts[value] || 0) + 1
    return counts
  }, {}), [logs])
  const levelCounts = useMemo(() => logs.reduce((counts, log) => {
    const value = String(log.level || 'unknown').toLowerCase()
    counts[value] = (counts[value] || 0) + 1
    return counts
  }, {}), [logs])
  const visibleLogs = source ? logs.filter(log => logSource(log) === source) : logs

  return (
    <div className="explorer-page">
      <ExplorerHeader title="Logs" description="Facet returned Loki entries by level and source, inspect structured labels, and follow trace context." />
      <form className="explorer-toolbar" onSubmit={submit}>
        <ExplorerField label="Search logs">
          <input value={search} onChange={event => setSearch(event.target.value)} placeholder="Message contains…" />
        </ExplorerField>
        <ExplorerField label="Level">
          <select value={level} onChange={event => setLevel(event.target.value)}>
            <option value="">All levels</option>
            {['error', 'warn', 'info', 'debug'].map(value => <option key={value} value={value}>{value}{levelCounts[value] ? ` (${levelCounts[value]})` : ''}</option>)}
          </select>
        </ExplorerField>
        <label className="timeline-toggle">
          <input type="checkbox" checked={liveTail} onChange={e => setLiveTail(e.target.checked)} />
          Live tail
        </label>
        <button className="explorer-button explorer-button--primary" type="submit">Run query</button>
      </form>
      <ExplorerSummary
        facets={[`service: ${query.service || 'all'}`, `level: ${query.level || 'all'}`, source && `source: ${source}`, query.search && `contains: ${query.search}`, filters.from && filters.to ? 'window: absolute' : `window: ${query.minutes}m`]}
        count={visibleLogs.length}
        noun="logs"
        loading={loading}
        live={effectiveRefresh > 0}
      />
      {query.service && <RelatedIncidentsStrip service={query.service} />}
      <div className="explorer-grid explorer-grid--sidebar">
        <aside className="explorer-card">
          <div className="explorer-section-heading"><div><h2>Source facets</h2><p>Labels present in this result set</p></div></div>
          <div className="explorer-list">
            <FacetButton active={!source} count={logs.length} onClick={() => { setSource(''); setUrlParams({ source: '' }) }}>All sources</FacetButton>
            {sources.map(value => (
              <FacetButton key={value} active={source === value} count={sourceCounts[value]} onClick={() => { setSource(value); setUrlParams({ source: value }) }}>{value}</FacetButton>
            ))}
          </div>
          <div className="explorer-section-heading" style={{ marginTop: 12 }}><div><h2>Levels</h2></div></div>
          <div className="explorer-list">
            {Object.entries(levelCounts).map(([value, count]) => (
              <FacetButton key={value} active={level === value} count={count} onClick={() => setLevel(value)}>{value}</FacetButton>
            ))}
          </div>
        </aside>
        <section className="explorer-card">
          <div className="explorer-section-heading"><div><h2>Returned events</h2><p>Formatted fields first; raw on demand</p></div></div>
          <ExplorerState loading={loading} error={error} dependency="Loki" empty={!visibleLogs.length} emptyTitle="No matching logs" emptyBody="Loki returned no entries for the selected scope.">
            <>
              <LogHistogram logs={visibleLogs} onBrush={brushZoom} />
              {visibleLogs.map((log, index) => <LogRow key={`${log.timestamp}-${index}`} log={log} index={index} />)}
            </>
          </ExplorerState>
        </section>
      </div>
    </div>
  )
}
