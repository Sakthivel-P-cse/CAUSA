import React, { useState } from 'react'

function FacetGroup({ name, label, values, selected, onToggle, collapsible = false, preview = 8 }) {
  const [expanded, setExpanded] = useState(!collapsible)
  const entries = Array.isArray(values)
    ? values.map(value => [String(value), null])
    : Object.entries(values || {}).sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))

  if (!entries.length) return null
  const visible = expanded ? entries : entries.slice(0, preview)

  return (
    <fieldset className="facet-group">
      <legend>{label}</legend>
      {visible.map(([value, count]) => (
        <label key={value} className="facet-option">
          <input
            type="checkbox"
            checked={selected === value}
            onChange={() => onToggle(name, selected === value ? '' : value)}
          />
          <span title={value}>{value || 'Unknown'}</span>
          {count != null && <span className="facet-count">{count}</span>}
        </label>
      ))}
      {collapsible && entries.length > preview && (
        <button type="button" className="link-button facet-more" onClick={() => setExpanded(v => !v)}>
          {expanded ? 'Show fewer' : `Show all (${entries.length})`}
        </button>
      )}
    </fieldset>
  )
}

export default function FacetPanel({ facets = {}, filters = {}, onChange, onClear }) {
  return (
    <aside className="facet-panel facet-panel--scroll" aria-label="Incident filters">
      <div className="facet-panel__header">
        <strong>Facets</strong>
        <button type="button" className="link-button" onClick={onClear}>Clear</button>
      </div>
      <FacetGroup name="severity" label="Severity" values={facets.severity} selected={filters.severity} onToggle={onChange} />
      <FacetGroup name="failure_type" label="Failure type" values={facets.failure_type} selected={filters.failure_type} onToggle={onChange} />
      <FacetGroup name="status" label="Status" values={facets.status} selected={filters.status} onToggle={onChange} />
      <FacetGroup
        name="service"
        label="Root service"
        values={facets.service}
        selected={filters.service}
        onToggle={onChange}
        collapsible
        preview={8}
      />
    </aside>
  )
}
