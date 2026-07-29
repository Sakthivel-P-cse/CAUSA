import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client.js'
import { apiQueryKey } from '../api/queries.js'

export function useFetch(url, deps = [], options = {}) {
  const refreshMs = Number(options.refreshMs || 0)
  const query = useQuery({
    queryKey: apiQueryKey(url, deps),
    queryFn: ({ signal }) => apiFetch(url, { signal }),
    enabled: Boolean(url),
    refetchInterval: refreshMs > 0 ? refreshMs : false,
    staleTime: Math.min(refreshMs || 15_000, 15_000),
    retry: options.retry ?? 1,
    retryDelay: 800,
  })

  const hasData = query.data !== undefined
  return {
    data: url ? (query.data ?? null) : null,
    loading: Boolean(url) && query.isPending && !hasData,
    fetching: Boolean(url) && query.isFetching,
    error: query.error ? (query.error.message || 'Request failed') : null,
    errorCode: query.error?.code || null,
    errorStatus: query.error?.status || null,
    lastUpdated: query.dataUpdatedAt || null,
    stale: Boolean(query.error && hasData),
    reload: query.refetch,
  }
}
