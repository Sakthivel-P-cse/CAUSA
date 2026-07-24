import React from 'react'

export default function AnomalySparkline({ values = [], label = 'Anomaly trend' }) {
  const clean = values.map(Number).filter(Number.isFinite)
  if (clean.length < 2) return <span className="sparkline-empty">No trend</span>
  const min = Math.min(...clean)
  const max = Math.max(...clean)
  const spread = Math.max(max - min, 0.0001)
  const points = clean.map((value, index) => {
    const x = (index / (clean.length - 1)) * 100
    const y = 28 - ((value - min) / spread) * 24
    return `${x},${y}`
  }).join(' ')
  return (
    <svg className="sparkline" viewBox="0 0 100 32" role="img" aria-label={label}>
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}
