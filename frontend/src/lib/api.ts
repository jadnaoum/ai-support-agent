import type { Catalog, DemoCustomer, OrderSpec, Product, Rules, Timeline } from './types'

export class ApiError extends Error {
  status: number
  code: string
  detail: unknown
  constructor(status: number, detail: unknown) {
    const code = typeof detail === 'object' && detail && 'code' in detail ? String((detail as { code: unknown }).code) : ''
    const message =
      typeof detail === 'string'
        ? detail
        : typeof detail === 'object' && detail && 'message' in detail
          ? String((detail as { message: unknown }).message)
          : `Request failed (${status})`
    super(message)
    this.status = status
    this.code = code
    this.detail = detail
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    })
  } catch {
    throw new ApiError(0, 'Could not reach the backend.')
  }
  if (!res.ok) {
    let detail: unknown = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? body
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

export const api = {
  catalog: () => request<Catalog>('/api/demo/catalog'),
  products: () => request<Product[]>('/api/products'),
  createCustomer: (body: { name: string; scenario_id?: string; orders?: OrderSpec[] }) =>
    request<DemoCustomer>('/api/demo/customer', { method: 'POST', body: JSON.stringify(body) }),
  customer: (id: string) => request<DemoCustomer>(`/api/demo/customer/${id}`),
  rules: (customerId: string) => request<Rules>(`/api/demo/rules?customer_id=${encodeURIComponent(customerId)}`),
  createConversation: (customerId: string) =>
    request<{ conversation_id: string }>('/api/conversations', {
      method: 'POST',
      body: JSON.stringify({ customer_id: customerId }),
    }),
  sendMessage: (conversationId: string, customerId: string, message: string) =>
    request<{ conversation_id: string; message_id: string }>('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId, customer_id: customerId, message }),
    }),
  endConversation: (conversationId: string) =>
    request<{ conversation_id: string; status: string }>(`/api/conversations/${conversationId}/end`, { method: 'POST' }),
  timeline: (conversationId: string) => request<Timeline>(`/api/conversations/${conversationId}/timeline`),
  submitCsat: (body: { conversation_id: string; rating: number; tags: string[]; free_text?: string }) =>
    request<{ conversation_id: string; rating: number; tags: string[] }>('/api/csat', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}

/** Copy a visitor can read for an ApiError. */
export function friendlyError(e: unknown): string {
  if (!(e instanceof ApiError)) return 'Something went wrong.'
  switch (e.code) {
    case 'message_too_long':
      return 'That message is too long for the demo. Keep it under 500 characters.'
    case 'conversation_cap':
      return "That's the message limit for one demo chat. Start a new chat to keep going."
    case 'conversation_closed':
      return 'This chat has ended.'
    case 'customer_cap':
      return "You've created a lot of demo accounts today. Try again tomorrow."
    case 'invalid_request':
      return e.message
  }
  if (e.status === 0) return 'Could not reach the backend. Is the server running?'
  if (e.status === 429) return 'The demo is busy right now. Give it a moment and try again.'
  if (e.status === 404) return 'Not found.'
  return e.message || 'Something went wrong.'
}
