import { describe, expect, it } from 'vitest'
import { apiQueryKey } from './queries.js'

describe('API query keys', () => {
  it('uses the same stable shape for fetch and prefetch consumers', () => {
    expect(apiQueryKey('/api/incidents/report-1', ['report-1'])).toEqual([
      'api',
      '/api/incidents/report-1',
      'report-1',
    ])
  })
})
