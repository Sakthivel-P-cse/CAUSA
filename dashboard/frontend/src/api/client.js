export class ApiError extends Error {
  constructor(status, code, message) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export async function apiFetch(path, { method = 'GET', body, signal, headers } = {}) {
  const opts = {
    method,
    signal,
    headers: {
      Accept: 'application/json',
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...headers,
    },
  }
  if (body !== undefined) opts.body = typeof body === 'string' ? body : JSON.stringify(body)

  const response = await fetch(path, opts)
  const text = await response.text()
  let data = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }

  if (!response.ok) {
    const detail = data?.detail
    const message =
      (typeof detail === 'object' && detail?.message) ||
      (typeof detail === 'string' && detail) ||
      response.statusText ||
      'Request failed'
    const code = (typeof detail === 'object' && detail?.code) || 'request_failed'
    throw new ApiError(response.status, code, message)
  }
  return data
}
