import React, { useState } from 'react'
import { confidenceBand, confidencePct } from '../../api/confidence.js'
import { formatAbstainReason } from '../../api/incidents.js'

export { confidenceBand, confidencePct }

export default function ConfidenceBadge({
  score,
  limited = false,
  showMeter = false,
  signals = null,
  calibrated = null,
  abstained = false,
  abstainReason = null,
  compact = false,
}) {
  const [open, setOpen] = useState(false)
  if (abstained) {
    const reason = formatAbstainReason(abstainReason)
    const shortReason = compact && reason.length > 42 ? `${reason.slice(0, 41)}…` : reason
    return (
      <div className="confidence-badge confidence-badge--abstained">
        <button
          type="button"
          className="confidence-badge__main"
          onClick={() => setOpen(v => !v)}
          aria-expanded={open}
          title={reason}
        >
          <strong>Inconclusive</strong>
          <span>{shortReason}</span>
        </button>
        {open && !compact && (
          <div className="confidence-badge__about" role="note">
            <p>RCA abstained — no root cause was ranked.</p>
            <p>{reason}</p>
          </div>
        )}
      </div>
    )
  }
  const pct = confidencePct(score)
  const band = confidenceBand(score)
  if (pct == null) {
    return <span className="confidence-badge confidence-badge--empty">—</span>
  }
  const calLabel = calibrated === true ? 'calibrated' : 'uncalibrated'
  return (
    <div className={`confidence-badge confidence-badge--${band}${limited ? ' confidence-badge--limited' : ''}`}>
      <button type="button" className="confidence-badge__main" onClick={() => setOpen(v => !v)} aria-expanded={open}>
        <strong>{pct}%</strong>
        <span>{band}{limited ? ' · weak' : ''}{calLabel ? ` · ${calLabel}` : ''}</span>
      </button>
      {showMeter && <meter min={0} max={1} value={Number(score) || 0} />}
      {open && (
        <div className="confidence-badge__about" role="note">
          <p>Margin confidence from ranking score — not a calibrated probability unless a fit map is loaded.</p>
          {signals && (
            <ul>
              <li>T (temporal): {Number(signals.T ?? 0).toFixed(2)}</li>
              <li>D (direction): {Number(signals.D ?? 0).toFixed(2)}</li>
              <li>P (propagation): {Number(signals.P ?? 0).toFixed(2)}</li>
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
