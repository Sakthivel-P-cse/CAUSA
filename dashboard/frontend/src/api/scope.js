export function applyDashboardScope(params, filters, { service = true, time = true } = {}) {
  const next = params instanceof URLSearchParams ? params : new URLSearchParams(params)
  if (service && filters.service) next.set('service', filters.service)
  if (time) {
    if (filters.from && filters.to) {
      next.set('start', filters.from)
      next.set('end', filters.to)
      next.delete('minutes')
    } else {
      next.set('minutes', String(filters.minutes))
      next.delete('start')
      next.delete('end')
    }
  }
  return next
}

export function scopeWindowLabel(filters) {
  return filters.from && filters.to ? 'absolute window' : `last ${filters.minutes}m`
}
