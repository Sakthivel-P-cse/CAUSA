import React from 'react'
import '../../styles/explorers.css'

export function ExplorerHeader({ eyebrow = 'Telemetry explorer', title, description, actions, children }) {
  return (
    <header className="explorer-header">
      <div className="explorer-header__copy">
        <span className="explorer-eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="explorer-header__actions">{actions}</div>}
      {children}
    </header>
  )
}

export function ExplorerSummary({ facets = [], count, noun = 'results', loading = false, live = false }) {
  return (
    <div className="explorer-summary" aria-live="polite">
      <div className="explorer-summary__facets" aria-label="Active query">
        {facets.filter(Boolean).map((facet, index) => (
          <span className="explorer-chip" key={`${facet}-${index}`}>{facet}</span>
        ))}
      </div>
      <strong className="explorer-result-count">
        {loading ? 'Loading…' : `${count ?? 0} ${noun}`}
        {live && !loading && <span className="explorer-live"><i /> current</span>}
      </strong>
    </div>
  )
}

export function ExplorerState({
  loading,
  error,
  empty,
  emptyTitle = 'No results',
  emptyBody = 'Try changing the active filters or time window.',
  children,
}) {
  if (error) {
    return (
      <div className="explorer-state explorer-state--error" role="alert">
        <strong>Telemetry could not be loaded</strong>
        <span>{typeof error === 'string' ? error : error?.message || 'Upstream unavailable'}</span>
        <span className="explorer-state__hint">Port-forward Prometheus :9090, Loki :3100, and Tempo :3200, then refresh.</span>
      </div>
    )
  }
  if (loading && empty) {
    return <div className="explorer-state" role="status"><span className="explorer-spinner" />Loading telemetry…</div>
  }
  if (empty) {
    return (
      <div className="explorer-state">
        <strong>{emptyTitle}</strong>
        <span>{emptyBody}</span>
      </div>
    )
  }
  return children
}

export function ExplorerField({ label, children, className = '' }) {
  return (
    <label className={`explorer-field ${className}`}>
      <span>{label}</span>
      {children}
    </label>
  )
}

export function FacetButton({ active, count, children, ...props }) {
  return (
    <button
      type="button"
      className={`explorer-facet${active ? ' is-active' : ''}`}
      aria-pressed={active}
      {...props}
    >
      <span>{children}</span>
      {count != null && <b>{count}</b>}
    </button>
  )
}

export function formatDuration(ms) {
  const value = Number(ms)
  if (!Number.isFinite(value)) return '—'
  if (value < 1) return `${(value * 1000).toFixed(0)} µs`
  if (value < 1000) return `${value.toFixed(value < 10 ? 2 : 1)} ms`
  return `${(value / 1000).toFixed(2)} s`
}

export function setUrlParams(updates, { replace = true } = {}) {
  const url = new URL(window.location.href)
  Object.entries(updates).forEach(([key, value]) => {
    if (value === '' || value == null || value === false) url.searchParams.delete(key)
    else url.searchParams.set(key, String(value))
  })
  window.history[replace ? 'replaceState' : 'pushState']({}, '', `${url.pathname}${url.search}${url.hash}`)
}

export function initialUrlParam(name, fallback = '') {
  if (typeof window === 'undefined') return fallback
  return new URLSearchParams(window.location.search).get(name) ?? fallback
}
