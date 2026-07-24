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
  const level = log.level?.toLowerCase() || 'unknown'
  const traceId = traceIdFor(log)
  const details = { ...log, labels: logLabels(log) }
  return (
    <details className="log-row">
      <summary aria-label={`Log ${index + 1}: ${log.message || 'no message'}`}>
        <time style={{ color: 'var(--muted-2)' }}>{formatLogTime(log.timestamp)}</time>
        <strong style={{ color: LEVEL_COLOUR[level] || 'var(--muted)', textTransform: 'uppercase' }}>{level}</strong>
        <span style={{ color: 'var(--accent-2)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{logSource(log)}</span>
        <span className="log-row__message">{log.message || '(no message)'}</span>
      </summary>
      <div className="log-row__detail">
        {traceId && <Link className="explorer-link" to={`/traces?trace=${encodeURIComponent(traceId)}`}>Open trace {String(traceId).slice(0, 16)}…</Link>}
        <pre>{JSON.stringify(details, null, 2)}</pre>
      </div>
    </details>
  )
}

function LogHistogram({ logs }) {
  const buckets = useMemo(() => {
    const times = logs.map(log => new Date(typeof log.timestamp === 'number' && log.timestamp < 1e12 ? log.timestamp * 1000 : log.timestamp).getTime()).filter(Number.isFinite)
    if (!times.length) return []
    const min = Math.min(...times)
    const max = Math.max(...times)
    const width = Math.max((max - min) / 24, 1)
    const result = Array.from({ length: 24 }, (_, index) => ({ start: min + index * width, count: 0 }))
    times.forEach(time => { result[Math.min(23, Math.floor((time - min) / width))].count += 1 })
    return result
  }, [logs])
  const highest = Math.max(1, ...buckets.map(bucket => bucket.count))
  return (
    <div>
      <div className="log-histogram" aria-label="Log volume histogram">
        {buckets.map((bucket, index) => (
          <span className="log-histogram__bar" key={index} style={{ height: `${Math.max(3, (bucket.count / highest) * 100)}%` }} title={`${bucket.count} logs around ${new Date(bucket.start).toLocaleTimeString()}`} />
        ))}
      </div>
      <p style={{ color: 'var(--muted-2)', fontSize: 10, margin: '8px 4px 12px' }}>Volume derived from returned log timestamps</p>
    </div>
  )
}

export default function Logs() {
  const { filters, refreshMs } = useExplorerScope()

  const urlService = initialUrlParam('service')
  const [level, setLevel] = useState(() => initialUrlParam('level'))
  const [source, setSource] = useState(() => initialUrlParam('source'))
  const [search, setSearch] = useState(() => initialUrlParam('search'))
  const [query, setQuery] = useState(() => ({
    service: filters.service || urlService,
    level: initialUrlParam('level'),
    search: initialUrlParam('search'),
    minutes: filters.minutes,
  }))

  useEffect(() => {
    setQuery(prev => ({ ...prev, service: filters.service || urlService, minutes: filters.minutes }))
  }, [filters.service, filters.minutes])

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
    { refreshMs }
  )

  function submit(e) {
    e.preventDefault()
    const service = filters.service || urlService
    setQuery({ service, level, search, minutes: filters.minutes })
    setUrlParams({ service, level, source, search })
  }

  const logs = logsRaw ?? []
  const sources = useMemo(() => Array.from(new Set(logs.map(logSource))).sort(), [logs])
  const sourceCounts = useMemo(() => logs.reduce((counts, log) => {
    const value = logSource(log)
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
            {['error', 'warn', 'info', 'debug'].map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </ExplorerField>
        <button className="explorer-button explorer-button--primary" type="submit">Run query</button>
      </form>
      <ExplorerSummary
        facets={[`service: ${query.service || 'all'}`, `level: ${query.level || 'all'}`, source && `source: ${source}`, query.search && `contains: ${query.search}`, filters.from && filters.to ? 'window: absolute' : `window: ${query.minutes}m`]}
        count={visibleLogs.length}
        noun="logs"
        loading={loading}
        live={refreshMs > 0}
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
        </aside>
        <section className="explorer-card">
          <div className="explorer-section-heading"><div><h2>Returned events</h2><p>Select a row to inspect its complete structured payload</p></div></div>
          <ExplorerState loading={loading} error={error} empty={!visibleLogs.length} emptyTitle="No matching logs" emptyBody="Loki returned no entries. If this persists, port-forward loki:3100 and widen the time window.">
            <>
              <LogHistogram logs={visibleLogs} />
              {visibleLogs.map((log, index) => <LogRow key={`${log.timestamp}-${index}`} log={log} index={index} />)}
            </>
          </ExplorerState>
        </section>
      </div>
    </div>
  )
}
