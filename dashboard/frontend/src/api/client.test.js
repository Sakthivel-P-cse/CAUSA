import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiFetch } from './client.js'

describe('apiFetch authentication', () => {
  afterEach(() => {
    window.sessionStorage.clear()
    vi.restoreAllMocks()
  })

  it('sends the bearer token from session storage', async () => {
    window.sessionStorage.setItem('causa_api_token', 'viewer-secret')
    const fetchMock = vi.spyOn(window, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(apiFetch('/api/stats')).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/stats',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer viewer-secret',
        }),
      }),
    )
  })
})
