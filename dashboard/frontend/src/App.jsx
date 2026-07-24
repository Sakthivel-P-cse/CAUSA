import React, { Suspense, lazy, useState } from 'react'
import { Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import {
  Activity, AlarmClock, Archive, BarChart3, BellRing, ChartNoAxesCombined,
  ChevronLeft, ChevronRight, CircleGauge, GitBranch, Menu, Moon, Network,
  RefreshCw, Settings2, Sun, UserRound, Wrench,
} from 'lucide-react'
import { useDashboard } from './context/DashboardContext.jsx'
import { Button, Drawer, LoadingState, Popover, Select, TimeRangePicker } from './components/ui/index.js'
import { useFetch } from './hooks/useFetch.js'

const Overview = lazy(() => import('./pages/Overview.jsx'))
const Metrics = lazy(() => import('./pages/Metrics.jsx'))
const Logs = lazy(() => import('./pages/Logs.jsx'))
const Traces = lazy(() => import('./pages/Traces.jsx'))
const Graphs = lazy(() => import('./pages/Graphs.jsx'))
const Statistics = lazy(() => import('./pages/Statistics.jsx'))
const IncidentInbox = lazy(() => import('./pages/IncidentInbox.jsx'))
const IncidentHistory = lazy(() => import('./pages/IncidentHistory.jsx'))
const IncidentInvestigation = lazy(() => import('./pages/IncidentInvestigation.jsx'))
const RemediationQueue = lazy(() => import('./pages/RemediationQueue.jsx'))
const RemediationReview = lazy(() => import('./pages/RemediationReview.jsx'))

const NAV_GROUPS = [
  { label: 'Watchdog', items: [
    { to: '/', label: 'Overview', icon: CircleGauge },
    { to: '/incidents', label: 'Incidents & RCA', icon: BellRing, end: true },
    { to: '/incidents/history', label: 'History', icon: Archive },
    { to: '/remediation', label: 'Remediation', icon: Wrench, end: true },
  ] },
  { label: 'Investigate', items: [
    { to: '/graphs', label: 'Graph Maps', icon: GitBranch },
  ] },
  { label: 'Explore', items: [
    { to: '/metrics', label: 'Metrics', icon: Activity },
    { to: '/logs', label: 'Logs', icon: ChartNoAxesCombined },
    { to: '/traces', label: 'Traces', icon: Network },
  ] },
  { label: 'Analytics', items: [{ to: '/stats', label: 'Statistics', icon: BarChart3 }] },
]

const PATH_LABELS = Object.fromEntries(NAV_GROUPS.flatMap(group => group.items.map(item => [item.to, item.label])))

function Navigation({ collapsed = false, onNavigate }) {
  return (
    <nav className="product-nav" aria-label="Primary">
      {NAV_GROUPS.map(group => (
        <section className="nav-group" key={group.label} aria-labelledby={`nav-${group.label}`}>
          <h2 id={`nav-${group.label}`}>{collapsed ? <span className="sr-only">{group.label}</span> : group.label}</h2>
          {group.items.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end || to === '/'}
              onClick={onNavigate}
              className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
              title={collapsed ? label : undefined}
            >
              <Icon size={17} aria-hidden="true" />
              <span className={collapsed ? 'sr-only' : ''}>{label}</span>
            </NavLink>
          ))}
        </section>
      ))}
    </nav>
  )
}

function GlobalControls() {
  const { filters, setFilter } = useDashboard()
  const queryClient = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const { data: services } = useFetch('/api/metrics/services', [])
  const serviceNames = (services || []).map(s => s.name).filter(Boolean).sort()

  async function refresh() {
    setRefreshing(true)
    window.dispatchEvent(new CustomEvent('watchdog:refresh'))
    await queryClient.invalidateQueries()
    setTimeout(() => setRefreshing(false), 350)
  }

  return (
    <section className="global-controls" aria-label="Global scope">
      <Select
        aria-label="Service scope"
        value={filters.service}
        onChange={event => setFilter('service', event.target.value)}
      >
        <option value="">All services</option>
        {filters.service && !serviceNames.includes(filters.service) && (
          <option value={filters.service}>{filters.service}</option>
        )}
        {serviceNames.map(name => (
          <option key={name} value={name}>{name}</option>
        ))}
      </Select>
      <TimeRangePicker filters={filters} setFilter={setFilter} />
      <Select aria-label="Auto refresh" value={filters.refreshSec} onChange={event => setFilter('refreshSec', Number(event.target.value))}>
        <option value={0}>Refresh off</option>
        <option value={15}>Every 15s</option>
        <option value={30}>Every 30s</option>
        <option value={60}>Every minute</option>
      </Select>
      <Button variant="ghost" onClick={refresh} aria-label="Refresh data" title="Refresh data">
        <RefreshCw size={17} className={refreshing ? 'ui-spin' : ''} />
        <span className="desktop-label">{refreshing ? 'Refreshing' : 'Refresh'}</span>
      </Button>
    </section>
  )
}

function UserMenu() {
  const { preferences, setPreference } = useDashboard()
  return (
    <Popover label="User preferences" trigger={<Button variant="ghost" aria-label="Open user menu"><UserRound size={18} /><span className="desktop-label">Preferences</span></Button>}>
      <div className="user-menu">
        <Button variant="ghost" role="menuitem" onClick={() => setPreference('theme', preferences.theme === 'dark' ? 'light' : 'dark')}>
          {preferences.theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          {preferences.theme === 'dark' ? 'Light theme' : 'Dark theme'}
        </Button>
        <Button variant="ghost" role="menuitem" onClick={() => setPreference('density', preferences.density === 'compact' ? 'comfortable' : 'compact')}>
          <Settings2 size={16} />
          {preferences.density === 'compact' ? 'Comfortable density' : 'Compact density'}
        </Button>
      </div>
    </Popover>
  )
}

function Breadcrumbs() {
  const location = useLocation()
  const base = location.pathname.startsWith('/incidents/history')
    ? '/incidents/history'
    : location.pathname.startsWith('/incidents/')
      ? '/incidents'
      : location.pathname
  const label = PATH_LABELS[base] || 'Watchdog'
  return (
    <nav aria-label="Breadcrumb" className="breadcrumbs">
      <ol>
        <li>Causa</li>
        {location.pathname !== '/' && <li aria-current="page">{label}</li>}
      </ol>
    </nav>
  )
}

function ProductShell() {
  const { preferences, setPreference } = useDashboard()
  const [mobileOpen, setMobileOpen] = useState(false)
  const collapsed = preferences.sidebarCollapsed
  return (
    <div className={`app-shell${collapsed ? ' app-shell--collapsed' : ''}`}>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside className="sidebar">
        <div className="brand"><AlarmClock aria-hidden="true" /><span className={collapsed ? 'sr-only' : ''}>Causa</span></div>
        <Navigation collapsed={collapsed} />
        <Button className="sidebar-toggle" variant="ghost" onClick={() => setPreference('sidebarCollapsed', !collapsed)} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          {collapsed ? <ChevronRight size={17} /> : <><ChevronLeft size={17} /> Collapse</>}
        </Button>
      </aside>
      <div className="shell-body">
        <header className="topbar">
          <Button className="mobile-menu-button" variant="ghost" onClick={() => setMobileOpen(true)} aria-label="Open navigation"><Menu /></Button>
          <Breadcrumbs />
          <GlobalControls />
          <UserMenu />
        </header>
        <main id="main-content" className="main-area" tabIndex="-1">
          <Suspense fallback={<LoadingState label="Loading page" />}>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/incidents" element={<IncidentInbox />} />
              <Route path="/incidents/history" element={<IncidentHistory />} />
              <Route path="/incidents/history/:incidentId" element={<IncidentHistory />} />
              <Route path="/incidents/:reportId" element={<IncidentInvestigation />} />
              <Route path="/remediation" element={<RemediationQueue />} />
              <Route path="/remediation/:planId" element={<RemediationReview />} />
              <Route path="/metrics" element={<Metrics />} />
              <Route path="/logs" element={<Logs />} />
              <Route path="/traces" element={<Traces />} />
              <Route path="/graphs" element={<Graphs />} />
              <Route path="/rca" element={<Navigate replace to="/incidents" />} />
              <Route path="/stats" element={<Statistics />} />
            </Routes>
          </Suspense>
        </main>
      </div>
      <Drawer open={mobileOpen} onClose={() => setMobileOpen(false)} title="Navigation">
        <Navigation onNavigate={() => setMobileOpen(false)} />
      </Drawer>
    </div>
  )
}

export default function App() {
  return <ProductShell />
}
