import React from 'react'

const DOT_CLASS = {
  red: 'status-dot status-dot--danger',
  yellow: 'status-dot status-dot--warn',
  green: 'status-dot status-dot--good',
  grey: 'status-dot',
}

const BADGE_CLASS = {
  red: 'status-chip status-critical',
  yellow: 'status-chip status-warning',
  green: 'status-chip status-complete',
  grey: 'status-chip',
}

export function StatusDot({ status }) {
  return <span className={DOT_CLASS[status] || DOT_CLASS.grey} aria-hidden="true" />
}

export function Card({ title, children, className = '', style }) {
  return (
    <div className={`ui-card ${className}`.trim()} style={style}>
      {title && <h3 className="ui-card__title">{title}</h3>}
      {children}
    </div>
  )
}

export function Badge({ label, colour, children }) {
  return (
    <span className={BADGE_CLASS[colour] || BADGE_CLASS.grey}>
      {children || label}
    </span>
  )
}

export function Spinner({ label }) {
  return <div className="ui-loading">{label || 'Loading...'}</div>
}

export function ErrorMsg({ msg, error }) {
  const text = msg || (typeof error === 'string' ? error : error?.message) || 'Request failed'
  return <div className="ui-error" role="alert">Error: {text}</div>
}

export function Table({ cols, rows, onRow }) {
  if (!rows || rows.length === 0) return <div className="ui-empty">No data.</div>

  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {cols.map(c => (
              <th key={c.key}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              onClick={() => onRow && onRow(row)}
              className={onRow ? 'is-clickable' : undefined}
            >
              {cols.map(c => (
                <td key={c.key}>
                  {c.render ? c.render(row[c.key], row) : row[c.key] ?? '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Drawer({ open, onClose, title, children, width = 700 }) {
  if (!open) return null
  return (
    <>
      <div className="ui-drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <div className="ui-drawer" style={{ width, maxWidth: '95vw' }} role="dialog" aria-modal="true" aria-label={title}>
        <div className="ui-drawer__head">
          <h2>{title}</h2>
          <button type="button" className="ui-drawer__close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </>
  )
}
