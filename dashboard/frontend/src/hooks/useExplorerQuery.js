import { useMemo } from 'react'
import { useFetch } from './useFetch.js'
import { useDashboard } from '../context/DashboardContext.jsx'

export function useExplorerScope() {
  const { filters, setFilter } = useDashboard()
  const refreshMs = filters.refreshSec > 0 ? filters.refreshSec * 1000 : 0
  return { filters, setFilter, refreshMs, serviceFilter: filters.service || '' }
}

export function filterByServiceName(items, serviceFilter, nameKey = 'name') {
  const needle = String(serviceFilter || '').trim().toLowerCase()
  if (!needle) return items || []
  return (items || []).filter(item => String(item?.[nameKey] || '').toLowerCase().includes(needle))
}

export function useExplorerQuery(url, deps = [], options = {}) {
  const { refreshMs, filters, serviceFilter } = useExplorerScope()
  const result = useFetch(url, deps, { ...options, refreshMs: options.refreshMs ?? refreshMs })
  const filtered = useMemo(() => {
    if (!options.filterKey || !Array.isArray(result.data)) return result.data
    return filterByServiceName(result.data, serviceFilter, options.filterKey)
  }, [result.data, serviceFilter, options.filterKey])

  return {
    ...result,
    data: options.filterKey ? filtered : result.data,
    refreshMs,
    filters,
    serviceFilter,
  }
}
