import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client.js'

export function useFetch(url, deps = [], options = {}) {
  const refreshMs = Number(options.refreshMs || 0)
  const query = useQuery({
    queryKey: ['legacy-api', url, ...deps],
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
    reload: query.refetch,
  }
}
