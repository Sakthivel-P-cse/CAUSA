import { QueryClient, useQuery } from '@tanstack/react-query'
import { apiFetch } from './client.js'

export const queryKeys = {
  all: ['watchdog'],
  resource: (resource, params = {}) => ['watchdog', resource, params],
  services: () => ['watchdog', 'services'],
  incidents: params => ['watchdog', 'incidents', params],
  incident: reportId => ['watchdog', 'incident', reportId],
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) => error?.status !== 401 && error?.status !== 403 && count < 2,
      staleTime: 15_000,
      refetchOnWindowFocus: false,
    },
  },
})

export function useApiQuery({ queryKey, url, enabled = true, refreshMs = 0, ...options }) {
  return useQuery({
    queryKey,
    queryFn: ({ signal }) => apiFetch(url, { signal }),
    enabled: Boolean(url) && enabled,
    refetchInterval: refreshMs > 0 ? refreshMs : false,
    ...options,
  })
}

