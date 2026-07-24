import React from 'react'

export default function HealthSummary({
  activeAlerts = 0,
  recentIncidents = 0,
  completeAnalyses = 0,
  needsInvestigation = 0,
  degradedServices = 0,
}) {
  const items = [
    ['Active alerts', activeAlerts],
    ['Recent incidents', recentIncidents],
    ['Complete analyses', completeAnalyses],
    ['Needs investigation', needsInvestigation],
    ['Degraded services', degradedServices],
  ]
  return (
    <section className="health-summary" aria-label="Operational health summary">
      {items.map(([label, value]) => (
        <div className="health-summary__item" key={label}>
          <strong>{value}</strong>
          <span>{label}</span>
        </div>
      ))}
    </section>
  )
}
