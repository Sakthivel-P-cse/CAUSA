export function confidenceBand(score) {
  if (score == null || Number.isNaN(Number(score))) return null
  const pct = Math.round(Number(score) * 100)
  if (pct >= 75) return 'high'
  if (pct >= 45) return 'moderate'
  return 'low'
}

export function confidencePct(score) {
  if (score == null || Number.isNaN(Number(score))) return null
  return Math.round(Number(score) * 100)
}
