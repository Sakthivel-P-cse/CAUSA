import React from 'react'
import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useFetch } from '../hooks/useFetch.js'
import { useDashboard } from '../context/DashboardContext.jsx'
import { ExplorerHeader, ExplorerState, ExplorerSummary } from '../components/explorer/Explorer.jsx'

const PIE_COLOURS = ['var(--accent-2)', 'var(--warn)', 'var(--danger)', 'var(--good)', 'var(--accent)', 'var(--info)', 'var(--muted-2)']
const TOOLTIP_STYLE = { background: 'var(--panel-2)', border: '1px solid var(--line)', fontSize: 12 }

export default function Statistics() {
  const { filters } = useDashboard()
  const refreshMs = filters.refreshSec > 0 ? filters.refreshSec * 1000 : 0
  const { data, loading, error } = useFetch('/api/stats', [], { refreshMs })

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
  const classified = pieData.reduce((sum, item) => sum + Number(item.value || 0), 0)
  const coverage = total > 0 ? Math.min(100, (classified / total) * 100) : 0

  return (
    <div className="explorer-page">
      <ExplorerHeader title="Statistics" description="Aggregate RCA analysis coverage, confidence, failure classifications, affected services, and observed report volume." />
      <ExplorerSummary facets={['scope: RCA reports', 'period: all time', 'trend: last 30 days']} count={total} noun="reports" loading={loading} live={refreshMs > 0} />
      <ExplorerState loading={loading} error={error} empty={!data || total === 0} emptyTitle="No analysis statistics">
        <>
          <div className="explorer-stat-grid">
            <div className="explorer-stat"><span>Total reports</span><strong>{total}</strong></div>
            <div className="explorer-stat"><span>Failure breakdown coverage</span><strong>{coverage.toFixed(1)}%</strong></div>
            <div className="explorer-stat"><span>Average confidence</span><strong>{data?.avg_confidence != null ? `${(Number(data.avg_confidence) * 100).toFixed(1)}%` : '—'}</strong></div>
            <div className="explorer-stat"><span>Failure types</span><strong>{pieData.length}</strong></div>
          </div>

          <section className="explorer-card" style={{ marginBottom: 16 }}>
            <div className="explorer-section-heading">
              <div><h2>Analysis coverage</h2><p>{classified} of {total} reports are represented in the returned failure-type breakdown.</p></div>
              <strong>{coverage.toFixed(1)}%</strong>
            </div>
            <progress value={classified} max={Math.max(total, 1)} style={{ width: '100%', accentColor: 'var(--accent)', height: 10 }} aria-label="Failure classification coverage" />
            <p style={{ color: 'var(--muted-2)', fontSize: 11, marginBottom: 0 }}>Confidence is available only as an aggregate average, so no synthetic confidence distribution is shown.</p>
          </section>

          <div className="explorer-grid explorer-grid--two">
            <section className="explorer-card">
              <div className="explorer-section-heading"><div><h2>Failure distribution</h2><p>Classifications returned by the statistics API</p></div></div>
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
              <div className="explorer-section-heading"><div><h2>Top affected services</h2><p>Root-cause services in returned reports</p></div></div>
              <ExplorerState empty={!topServices.length} emptyTitle="No affected services">
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={topServices} layout="vertical">
                    <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: 'var(--muted-2)' }} />
                    <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 11, fill: 'var(--muted)' }} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Bar dataKey="count" fill="var(--accent-2)" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </ExplorerState>
            </section>
          </div>

          <section className="explorer-card" style={{ marginTop: 16 }}>
            <div className="explorer-section-heading"><div><h2>Reports per day</h2><p>Observed RCA report count over the last 30 days</p></div></div>
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
