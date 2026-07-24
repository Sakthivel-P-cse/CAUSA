import React from 'react'
import { confidenceBand, confidencePct } from '../../api/confidence.js'

export { confidenceBand, confidencePct }

export default function ConfidenceBadge({ score, limited = false, showMeter = false }) {
  const pct = confidencePct(score)
  const band = confidenceBand(score)
  if (pct == null) {
    return <span className="confidence-badge confidence-badge--empty">No confidence</span>
  }
  return (
    <div className={`confidence-badge confidence-badge--${band}${limited ? ' confidence-badge--limited' : ''}`} aria-label="Confidence">
      <strong>{pct}%</strong>
      <span>{band}{limited ? ' · weak signal' : ''}</span>
      {showMeter && <meter min={0} max={1} value={Number(score) || 0} />}
    </div>
  )
}
