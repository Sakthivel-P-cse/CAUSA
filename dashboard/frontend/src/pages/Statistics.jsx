import React, { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'
import { useFetch } from '../hooks/useFetch.js'
import { useDashboard } from '../context/DashboardContext.jsx'
import { ExplorerHeader, ExplorerState, ExplorerSummary } from '../components/explorer/Explorer.jsx'
import { applyDashboardScope, scopeWindowLabel } from '../api/scope.js'

const PIE_COLOURS = ['var(--accent-2)', 'var(--warn)', 'var(--danger)', 'var(--good)', 'var(--accent)', 'var(--info)', 'var(--muted-2)']
const TOOLTIP_STYLE = { background: 'var(--panel-2)', border: '1px solid var(--line)', fontSize: 12 }

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return '—'
  const s = Math.max(0, Number(seconds))
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  return `${(s / 3600).toFixed(1)}h`
}

export default function Statistics() {
  const { filters } = useDashboard()
  const refreshMs = filters.refreshSec > 0 ? filters.refreshSec * 1000 : 0
  const params = applyDashboardScope(new URLSearchParams(), filters)
  const { data, loading, error } = useFetch(`/api/stats?${params}`, [filters.service, filters.minutes, filters.from, filters.to], { refreshMs })

  const pieData = Object.entries(data?.by_failure_type ?? {}).map(([name, value]) => ({ name, value }))
  const topServices = (data?.top_services ?? []).map((row) => (
    Array.isArray(row)
      ? { name: row[0], count: row[1] }
      : { name: row.service || row.name, count: row.count }
  ))
  const perDay = (data?.incidents_per_day ?? []).map((row) => {
    if (Array.isArray(row)) return { day: String(row[0]).slice(5), count: row[1] }
    return { day: String(row.day || '').slice(5), count: row.count }
  })
  const total = data?.total ?? 0
  const calPoints = useMemo(() => (data?.feedback_calibration || []).map(p => ({
    x: Number(p.predicted),
    y: Number(p.correct),
  })), [data])
  const remRate = data?.remediation_success_rate

  return (
    <div className="explorer-page">
      <ExplorerHeader title="Statistics" description="How the RCA system is performing — ops KPIs, accuracy feedback, and failure mix." />
      <ExplorerSummary facets={['scope: RCA reports', `service: ${filters.service || 'all'}`, scopeWindowLabel(filters), 'ops KPIs']} count={total} noun="reports" loading={loading} live={refreshMs > 0} />
      <ExplorerState loading={loading} error={error} empty={!data || total === 0} emptyTitle="No analysis statistics">
        <>
          <div className="explorer-stat-grid">
            <div className="explorer-stat"><span>MTTA</span><strong>{formatDuration(data?.mtta_seconds)}</strong></div>
            <div className="explorer-stat"><span>MTTR</span><strong>{formatDuration(data?.mttr_seconds)}</strong></div>
            <div className="explorer-stat"><span>Remediation success</span><strong>{remRate == null ? '—' : `${Math.round(remRate * 100)}%`}</strong></div>
            <div className="explorer-stat">
              <span>Mean ranking confidence</span>
              <strong>{data?.avg_confidence != null ? Number(data.avg_confidence).toFixed(3) : '—'}</strong>
              <small>{data?.confidence_calibrated ? 'Calibrated probability' : `Uncalibrated · n=${data?.confidence_sample_size || 0}`}</small>
            </div>
            <div className="explorer-stat"><span>Complete</span><strong>{data?.analyses_complete ?? '—'}</strong></div>
            <div className="explorer-stat"><span>Degraded</span><strong>{data?.analyses_degraded ?? '—'}</strong></div>
            <div className="explorer-stat"><span>Inconclusive</span><strong>{data?.analyses_inconclusive ?? '—'}</strong></div>
            <div className="explorer-stat"><span>Active</span><strong>{data?.active_incidents ?? '—'}</strong></div>
          </div>

          <div className="explorer-grid explorer-grid--two">
            <section className="explorer-card">
              <div className="explorer-section-heading"><div><h2>Failure distribution</h2><p>Click a service bar to open filtered incidents</p></div></div>
              <ExplorerState empty={!pieData.length} emptyTitle="No failure classifications">
                <ResponsiveContainer width="100%" height={240}>
                  <PieChart>
                    <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={88} label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}>
                      {pieData.map((item, index) => <Cell key={item.name} fill={PIE_COLOURS[index % PIE_COLOURS.length]} />)}
                    </Pie>
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                  </PieChart>
                </ResponsiveContainer>
              </ExplorerState>
            </section>

            <section className="explorer-card">
              <div className="explorer-section-heading"><div><h2>Top root-cause services</h2><p>Drill into Incidents</p></div></div>
              <ExplorerState empty={!topServices.length} emptyTitle="No affected services">
                <ul className="compact-list">
                  {topServices.map(row => (
                    <li key={row.name}>
                      <Link to={`/incidents?service=${encodeURIComponent(row.name)}`}>{row.name}</Link>
                      <strong>{row.count}</strong>
                    </li>
                  ))}
                </ul>
              </ExplorerState>
            </section>
          </div>

          <section className="explorer-card" style={{ marginTop: 16 }}>
            <div className="explorer-section-heading">
              <div>
                <h2>Confidence calibration</h2>
                <p>Predicted confidence vs accept/reject feedback (needs feedback loop data)</p>
              </div>
            </div>
            <ExplorerState empty={!calPoints.length} emptyTitle="No feedback yet — accept/reject RCA on investigations to populate">
              <ResponsiveContainer width="100%" height={220}>
                <ScatterChart>
                  <XAxis type="number" dataKey="x" name="predicted" domain={[0, 1]} tick={{ fontSize: 11, fill: 'var(--muted-2)' }} />
                  <YAxis type="number" dataKey="y" name="accepted" domain={[0, 1]} tick={{ fontSize: 11, fill: 'var(--muted-2)' }} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <Scatter data={calPoints} fill="var(--accent-2)" />
                </ScatterChart>
              </ResponsiveContainer>
            </ExplorerState>
          </section>

          <section className="explorer-card" style={{ marginTop: 16 }}>
            <div className="explorer-section-heading"><div><h2>Reports per day</h2></div></div>
            <ExplorerState empty={!perDay.length} emptyTitle="No recent reports">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={perDay}>
                  <XAxis dataKey="day" tick={{ fontSize: 10, fill: 'var(--muted-2)' }} interval="preserveStartEnd" />
                  <YAxis tick={{ fontSize: 11, fill: 'var(--muted-2)' }} allowDecimals={false} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <Bar dataKey="count" fill="var(--info)" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </ExplorerState>
          </section>
        </>
      </ExplorerState>
    </div>
  )
}
