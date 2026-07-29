import { describe, expect, it } from 'vitest'
import { applyDashboardScope, scopeWindowLabel } from './scope.js'

describe('dashboard scope', () => {
  it('uses an absolute range without also sending minutes', () => {
    const params = applyDashboardScope(new URLSearchParams(), {
      service: 'checkout',
      minutes: 360,
      from: '2026-01-01T00:00:00.000Z',
      to: '2026-01-01T01:00:00.000Z',
    })
    expect(params.get('service')).toBe('checkout')
    expect(params.get('start')).toBe('2026-01-01T00:00:00.000Z')
    expect(params.get('end')).toBe('2026-01-01T01:00:00.000Z')
    expect(params.has('minutes')).toBe(false)
    expect(scopeWindowLabel({ from: 'a', to: 'b' })).toBe('absolute window')
  })

  it('uses the relative window when absolute bounds are absent', () => {
    const params = applyDashboardScope(new URLSearchParams(), {
      service: '',
      minutes: 30,
      from: '',
      to: '',
    })
    expect(params.toString()).toBe('minutes=30')
  })
})
