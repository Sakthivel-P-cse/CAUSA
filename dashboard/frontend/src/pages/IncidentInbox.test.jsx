import React from 'react'
import { act, cleanup, render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import IncidentInbox from './IncidentInbox.jsx'

const inbox = vi.hoisted(() => ({ urls: [], filters: null }))

vi.mock('../hooks/useFetch.js', () => ({
  useFetch: url => {
    inbox.urls.push(url)
    return { data: { items: [], total: 0, facets: {} }, loading: false, error: null, reload: async () => {} }
  },
}))

vi.mock('../context/DashboardContext.jsx', () => ({
  useDashboard: () => ({ filters: inbox.filters }),
}))

vi.mock('../hooks/useIncidentStream.js', () => ({
  useIncidentStream: () => 'off',
}))

function renderInbox(filters) {
  inbox.filters = filters
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <IncidentInbox />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function latestBounds() {
  const params = new URLSearchParams(inbox.urls[inbox.urls.length - 1].split('?')[1])
  return { start: params.get('start'), end: params.get('end') }
}

describe('IncidentInbox time windows', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2025-03-01T12:05:40.000Z'))
    inbox.urls.length = 0
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('advances relative bounds at the next minute and clears the timer on unmount', () => {
    const { unmount } = renderInbox({ from: '', to: '', minutes: 15, service: '' })

    expect(latestBounds()).toEqual({
      start: '2025-03-01T11:50:00.000Z',
      end: '2025-03-01T12:05:59.999Z',
    })

    act(() => vi.advanceTimersByTime(20_000))

    expect(latestBounds()).toEqual({
      start: '2025-03-01T11:51:00.000Z',
      end: '2025-03-01T12:06:59.999Z',
    })
    expect(vi.getTimerCount()).toBe(1)

    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('keeps explicit from/to bounds fixed without a timer', () => {
    renderInbox({
      from: '2025-03-01T11:00:00.000Z',
      to: '2025-03-01T11:15:00.000Z',
      minutes: 15,
      service: '',
    })
    const initial = latestBounds()

    expect(initial).toEqual({
      start: '2025-03-01T11:00:00.000Z',
      end: '2025-03-01T11:15:00.000Z',
    })
    expect(vi.getTimerCount()).toBe(0)

    act(() => vi.advanceTimersByTime(120_000))

    expect(latestBounds()).toEqual(initial)
    expect(vi.getTimerCount()).toBe(0)
  })
})
