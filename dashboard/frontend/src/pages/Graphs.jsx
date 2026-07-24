import React, { useEffect, useState } from 'react'
import { useFetch } from '../hooks/useFetch.js'
import TopologyGraph from '../components/TopologyGraph.jsx'
import { useDashboard } from '../context/DashboardContext.jsx'
import {
  ExplorerField,
  ExplorerHeader,
  ExplorerState,
  ExplorerSummary,
  initialUrlParam,
  setUrlParams,
} from '../components/explorer/Explorer.jsx'

const TABS = [
  { id: 'services', label: 'Services', title: 'Service dependency map', description: 'Current request paths, traffic, latency, and errors.', legend: ['Service', 'Request edge', 'Error edge'] },
  { id: 'infrastructure', label: 'Infrastructure', title: 'Infrastructure map', description: 'Current node, pod, and service placement.', legend: ['Node', 'Pod', 'Service'] },
  { id: 'trace', label: 'Trace execution', title: 'Trace execution map', description: 'A selected trace snapshot ordered by span calls.', legend: ['Span', 'Successful call', 'Error span'] },
  { id: 'incident', label: 'Incident', title: 'Incident subgraph', description: 'Evidence around a selected RCA report.', legend: ['Suspected origin', 'Evidence node', 'Related edge'] },
]

function validTab(value) {
  return TABS.some(tab => tab.id === value) ? value : 'services'
}

export default function Graphs() {
  const { filters } = useDashboard()
  const refreshMs = filters.refreshSec > 0 ? filters.refreshSec * 1000 : 0
  const serviceQuery = filters.service ? `&service=${encodeURIComponent(filters.service)}` : ''

  const [activeTab, setActiveTab] = useState(() => validTab(initialUrlParam('map')))
  const [selectedTrace, setSelectedTrace] = useState('')
  const [selectedReport, setSelectedReport] = useState('')
  const traces = useFetch(activeTab === 'trace' ? `/api/traces?minutes=${filters.minutes}&limit=20${serviceQuery}` : null, [activeTab, filters.service, filters.minutes], { refreshMs })
  const reports = useFetch(activeTab === 'incident' ? `/api/incidents?limit=20${serviceQuery}` : null, [activeTab, filters.service], { refreshMs })
  const reportItems = Array.isArray(reports.data) ? reports.data : (reports.data?.items || [])

  useEffect(() => {
    const next = traces.data?.[0] || ''
    setSelectedTrace(current => (current && traces.data?.includes(current) ? current : next))
  }, [traces.data])

  useEffect(() => {
    const firstReportId = reportItems[0]?.id || ''
    setSelectedReport(current => (current && reportItems.some(report => report.id === current) ? current : firstReportId))
  }, [reportItems])

  const graphUrl = activeTab === 'services'
    ? '/api/graphs/service-dependency'
    : activeTab === 'infrastructure'
      ? '/api/graphs/infrastructure'
      : activeTab === 'trace' && selectedTrace
        ? `/api/graphs/trace-execution?trace_id=${encodeURIComponent(selectedTrace)}`
        : activeTab === 'incident' && selectedReport
          ? `/api/graphs/incident?report_id=${encodeURIComponent(selectedReport)}`
          : null
  const graph = useFetch(graphUrl, [activeTab, selectedTrace, selectedReport], { refreshMs })
  const tab = TABS.find(item => item.id === activeTab)
  const picker = activeTab === 'trace' ? traces : activeTab === 'incident' ? reports : null
  const missingSelection = (activeTab === 'trace' && !selectedTrace) || (activeTab === 'incident' && !selectedReport)

  function chooseTab(id) {
    setActiveTab(id)
    setUrlParams({ map: id })
  }

  return (
    <div className="explorer-page">
      <ExplorerHeader title="Graph maps" description="Switch between topology layers while rendering only the active map." />
      <ExplorerSummary facets={[`map: ${tab.label}`, `service: ${filters.service || 'all'}`, `window: ${filters.minutes}m`]} count={graph.data?.nodes?.length || 0} noun="nodes" loading={graph.loading || picker?.loading} live={refreshMs > 0 && ['services', 'infrastructure'].includes(activeTab)} />
      <nav className="explorer-tabs" aria-label="Graph maps">
        {TABS.map(item => (
          <button key={item.id} type="button" role="tab" aria-selected={activeTab === item.id} className={`explorer-tab${activeTab === item.id ? ' is-active' : ''}`} onClick={() => chooseTab(item.id)}>
            {item.label}
          </button>
        ))}
      </nav>
      <section className="explorer-card">
        <div className="explorer-section-heading">
          <div><h2>{tab.title}</h2><p>{tab.description}</p></div>
          <span className="explorer-chip">{['services', 'infrastructure'].includes(activeTab) ? 'live current state' : 'selected snapshot'}</span>
        </div>
        {activeTab === 'trace' && (
          <div className="explorer-toolbar">
            <ExplorerField label="Recent trace">
              <select value={selectedTrace} onChange={event => setSelectedTrace(event.target.value)}>
                <option value="">Select a trace</option>
                {(traces.data || []).map(traceId => <option key={traceId} value={traceId}>{traceId}</option>)}
              </select>
            </ExplorerField>
            <span className="explorer-chip">Filtered by current service and window</span>
          </div>
        )}
        {activeTab === 'incident' && (
          <div className="explorer-toolbar">
            <ExplorerField label="RCA report">
              <select value={selectedReport} onChange={event => setSelectedReport(event.target.value)}>
                <option value="">Select a report</option>
                {reportItems.map(report => <option key={report.id} value={report.id}>{report.alert_name || 'alert'} · {report.root_cause_service || report.alerted_service || 'unknown'}</option>)}
              </select>
            </ExplorerField>
            <span className="explorer-chip">Incident evidence snapshot</span>
          </div>
        )}
        <div className="explorer-actions" aria-label="Map legend" style={{ marginBottom: 12 }}>
          {tab.legend.map(item => <span className="explorer-chip" key={item}>{item}</span>)}
        </div>
        <div className="explorer-graph-frame" role="tabpanel">
          <ExplorerState
            loading={graph.loading || picker?.loading}
            error={picker?.error || graph.error}
            empty={missingSelection || !graph.data?.nodes?.length}
            emptyTitle={missingSelection ? `Select a ${activeTab === 'trace' ? 'trace' : 'report'}` : 'No graph data'}
          >
            <TopologyGraph graph={graph.data} height={520} />
          </ExplorerState>
        </div>
      </section>
    </div>
  )
}