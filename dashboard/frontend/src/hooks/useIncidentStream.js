import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

export function useIncidentStream(enabled = true) {
  const queryClient = useQueryClient()
  const esRef = useRef(null)
  const [status, setStatus] = useState(enabled ? 'connecting' : 'off')

  useEffect(() => {
    if (!enabled || typeof EventSource === 'undefined') return undefined
    const es = new EventSource('/api/incidents/stream')
    esRef.current = es
    let fallbackTimer = null
    const invalidate = () => {
      queryClient.invalidateQueries({
        predicate: query => (
          query.queryKey?.[0] === 'api'
          && String(query.queryKey?.[1] || '').startsWith('/api/incidents')
        ),
      })
    }
    es.onopen = () => {
      setStatus('live')
      if (fallbackTimer) window.clearInterval(fallbackTimer)
      fallbackTimer = null
    }
    es.addEventListener('incidents', invalidate)
    es.onerror = () => {
      setStatus('polling')
      if (!fallbackTimer) fallbackTimer = window.setInterval(invalidate, 15_000)
    }
    return () => {
      es.close()
      if (fallbackTimer) window.clearInterval(fallbackTimer)
      esRef.current = null
    }
  }, [enabled, queryClient])

  return status
}
