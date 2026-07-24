import React, { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Area, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from 'recharts'
import { useExplorerQuery, useExplorerScope } from '../hooks/useExplorerQuery.js'
import { StatusDot } from '../components/Common.jsx'
import {
  ExplorerHeader,
  ExplorerState,
  ExplorerSummary,
  initialUrlParam,
  setUrlParams,
} from '../components/explorer/Explorer.jsx'
import RelatedIncidentsStrip from '../components/watchdog/RelatedIncidentsStrip.jsx'

const METRICS = {
  error_rate: { title: 'Error rate', unit: '%', scale: 100, color: 'var(--danger)', threshold: 0.05 },
  latency_p99_ms: { title: 'p99 latency', unit: 'ms', scale: 1, color: 'var(--warn)', threshold: 2000 },
  request_rate: { title: 'Request rate', unit: 'req/s', scale: 1, color: 'var(--accent-2)' },
}

function formatMetric(value, config, digits = 2) {
  if (!Number.isFinite(Number(value))) return '—'
  return `${(Number(value) * config.scale).toFixed(digits)} ${config.unit}`
}

function buildChartData(series, config) {
  const points = (series ?? []).map(([timestamp, value]) => ({
    timestamp: Number(timestamp) * 1000,
    observed: Number(value),
  })).filter(point => Number.isFinite(point.observed) && Number.isFinite(point.timestamp))

  return points.map((point, index) => {
    const window = points.slice(Math.max(0, index - 6), index)
    const baseline = window.length
      ? window.reduce((total, item) => total + item.observed, 0) / window.length
      : point.observed
    const floor = config.unit === '%' ? 0.005 : config.unit === 'ms' ? 100 : 0.5
    const isAnomaly = index > 1 && Math.abs(point.observed - baseline) > Math.max(Math.abs(baseline) * 0.3, floor)
    return { ...point, baseline, anomaly: isAnomaly ? point.observed : null }
  })
}

function MetricTooltip({ active, payload, label, config }) {
  if (!active || !payload?.length) return null
  const row = payload[0]?.payload
  return (
    <div className="explorer-card" style={{ padding: 10, fontSize: 11 }}>
      <strong>{new Date(label).toLocaleString()}</strong>
      <div>Observed: {formatMetric(row.observed, config, config.unit === 'ms' ? 1 : 2)}</div>
      <div>Rolling baseline: {formatMetric(row.baseline, config, config.unit === 'ms' ? 1 : 2)}</div>
      {row.anomaly != null && <div style={{ color: 'var(--danger)' }}>Large baseline deviation</div>}
    </div>
  )
}

function MetricChart({ metric, seriesData, loading }) {
  const config = METRICS[metric]
  const chartData = useMemo(() => buildChartData(seriesData, config), [seriesData, config])
  const latest = chartData.at(-1)
  const delta = latest?.baseline
    ? ((latest.observed - latest.baseline) / Math.abs(latest.baseline)) * 100
    : 0
  const anomalyCount = chartData.filter(point => point.anomaly != null).length
  const caption = latest
    ? `Current ${formatMetric(latest.observed, config, config.unit === 'ms' ? 1 : 2)} · Baseline ${formatMetric(latest.baseline, config, config.unit === 'ms' ? 1 : 2)} · ${delta >= 0 ? '+' : ''}${delta.toFixed(1)}% vs baseline`
    : 'No current sample'

  return (
    <section className="explorer-card">
      <div className="explorer-section-heading">
        <div>
          <h2>{config.title}</h2>
          <p className="metric-caption">{caption}</p>
        </div>
        <span className="explorer-chip">{anomalyCount} deviations</span>
      </div>
      <ExplorerState loading={loading} empty={!chartData.length} emptyTitle={`No ${config.title.toLowerCase()} samples`}>
        <div className="chart-shell" aria-label={`${config.title} time series`}>
          <ResponsiveContainer width="100%" height={230}>
            <ComposedChart data={chartData} margin={{ top: 8, right: 10, left: 4, bottom: 0 }}>
              <XAxis dataKey="timestamp" type="number" domain={['dataMin', 'dataMax']} tickFormatter={value => new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} tick={{ fontSize: 10, fill: 'var(--muted-2)' }} />
              <YAxis tickFormatter={value => formatMetric(value, config, config.unit === 'ms' ? 0 : 1)} tick={{ fontSize: 10, fill: 'var(--muted-2)' }} width={68} />
              <Tooltip content={<MetricTooltip config={config} />} />
              {config.threshold != null && <ReferenceLine y={config.threshold} stroke="var(--danger)" strokeDasharray="4 3" label={{ value: 'threshold', fill: 'var(--danger)', fontSize: 10 }} />}
              <Area type="monotone" dataKey="baseline" fill="rgba(142,164,188,.12)" stroke="var(--muted-2)" strokeDasharray="4 3" />
              <Line type="monotone" dataKey="observed" stroke={config.color} strokeWidth={2} dot={false} />
              <Scatter dataKey="anomaly" fill="var(--danger)" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </ExplorerState>
    </section>
  )
}

export default function Metrics() {
  const { filters, refreshMs } = useExplorerScope()

  const { data: servicesRaw, loading, error } = useExplorerQuery('/api/metrics/services', [], { filterKey: 'name' })
  const [selected, setSelected] = useState(() => initialUrlParam('service'))

  const services = servicesRaw ?? []

  const chosenService = filters.service || selected || services[0]?.name || null
  const currentStats = services?.find(s => s.name === chosenService) ?? null

  const encSvc = chosenService ? encodeURIComponent(chosenService) : null
  const baseUrl = encSvc ? `/api/metrics/service/${encSvc}/series?minutes=${filters.minutes}&metric=` : null

  const errSeries = useExplorerQuery(baseUrl ? `${baseUrl}error_rate` : null, [chosenService, filters.minutes])
  const latSeries = useExplorerQuery(baseUrl ? `${baseUrl}latency_p99_ms` : null, [chosenService, filters.minutes])
  const rpsSeries = useExplorerQuery(baseUrl ? `${baseUrl}request_rate` : null, [chosenService, filters.minutes])
  const seriesError = errSeries.error || latSeries.error || rpsSeries.error
  const pointCount = (errSeries.data?.series?.length || 0) + (latSeries.data?.series?.length || 0) + (rpsSeries.data?.series?.length || 0)

  function chooseService(name) {
    setSelected(name)
    setUrlParams({ service: name })
  }

  return (
    <div className="explorer-page">
      <ExplorerHeader
        title="Metrics"
        description="Compare current service signals with rolling baselines and pivot directly into incident context."
        actions={chosenService && <div className="explorer-pivots"><Link className="explorer-link" to={`/incidents?service=${encodeURIComponent(chosenService)}`}>Related incidents</Link><Link className="explorer-link" to={`/logs?service=${encodeURIComponent(chosenService)}`}>Logs</Link><Link className="explorer-link" to={`/traces?service=${encodeURIComponent(chosenService)}`}>Traces</Link></div>}
      />
      <ExplorerSummary facets={[`service: ${chosenService || 'all'}`, `window: ${filters.minutes}m`, 'baseline: 6 points']} count={pointCount} noun="samples" loading={loading || errSeries.loading || latSeries.loading || rpsSeries.loading} live={refreshMs > 0} />
      <ExplorerState loading={loading} error={error} empty={!loading && services.length === 0} emptyTitle="No metric services" emptyBody="Prometheus returned no service edges. Port-forward prometheus:9090 after ABC + OTel Demo are up.">
        <div className="explorer-grid explorer-grid--sidebar">
          <aside className="explorer-card">
            <div className="explorer-section-heading"><div><h2>Services</h2><p>{services.length} in scope</p></div></div>
            <ul className="explorer-list">
              {services.map(service => (
                <li key={service.name}>
                  <button className={`explorer-row-button${chosenService === service.name ? ' is-active' : ''}`} type="button" aria-pressed={chosenService === service.name} onClick={() => chooseService(service.name)}>
                    <StatusDot status={service.health} />{service.name}
                  </button>
                </li>
              ))}
            </ul>
          </aside>
          <main className="explorer-grid">
            <RelatedIncidentsStrip service={chosenService} />
            {currentStats && (
              <div className="explorer-stat-grid">
                <div className="explorer-stat"><span>Error rate</span><strong>{formatMetric(currentStats.error_rate, METRICS.error_rate)}</strong></div>
                <div className="explorer-stat"><span>p99 latency</span><strong>{formatMetric(currentStats.latency_p99_ms, METRICS.latency_p99_ms, 0)}</strong></div>
                <div className="explorer-stat"><span>Request rate</span><strong>{formatMetric(currentStats.request_rate, METRICS.request_rate)}</strong></div>
                <div className="explorer-stat"><span>Health</span><strong>{currentStats.health}</strong></div>
              </div>
            )}
            {seriesError && <ExplorerState error={seriesError} empty />}
            <MetricChart metric="error_rate" seriesData={errSeries.data?.series} loading={errSeries.loading} />
            <MetricChart metric="latency_p99_ms" seriesData={latSeries.data?.series} loading={latSeries.loading} />
            <MetricChart metric="request_rate" seriesData={rpsSeries.data?.series} loading={rpsSeries.loading} />
          </main>
        </div>
      </ExplorerState>
    </div>
  )
}
