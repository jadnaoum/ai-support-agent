import { useCallback, useEffect, useRef, useState } from 'react'
import { api, friendlyError } from '@/lib/api'
import { streamTurn } from './useSSE'
import type { Check, Csat, DemoCustomer, PipelineEvent, Timeline, Totals, TurnRecord } from '@/lib/types'

export interface ChatMessage {
  id: string
  role: 'customer' | 'agent' | 'greeting'
  content: string
  turnIndex?: number
  latencyMs?: number | null
  llmCalls?: number | null
  streaming?: boolean
}

export type TurnStatus = 'queued' | 'running' | 'streaming' | 'done' | 'error'

export interface TurnView {
  index: number
  customerMessage: string
  record: TurnRecord | null
  /** Steps received while the turn is still running (before `record`). */
  liveEvents: PipelineEvent[]
  status: TurnStatus
  queuePosition: number
  startedAt: number
}

export type Phase = 'loading' | 'chatting' | 'ended' | 'rated' | 'missing'

export interface ConversationState {
  customer: DemoCustomer | null
  status: Timeline['conversation']['status']
  messages: ChatMessage[]
  turns: TurnView[]
  phase: Phase
  escalated: boolean
  busy: boolean
  error: string | null
  totals: Totals
  checks: Check[]
  csat: Csat | null
}

export const EMPTY_TOTALS: Totals = {
  messages: 0,
  turns: 0,
  latency_ms: 0,
  llm_calls: 0,
  cost_usd: null,
  cost_basis: 'unavailable',
}

const INITIAL: ConversationState = {
  customer: null,
  status: 'active',
  messages: [],
  turns: [],
  phase: 'loading',
  escalated: false,
  busy: false,
  error: null,
  totals: EMPTY_TOTALS,
  checks: [],
  csat: null,
}

export function totalsFrom(records: TurnRecord[]): Totals {
  const priced = records.filter((r) => r.cost_usd != null)
  const cost_basis: Totals['cost_basis'] =
    priced.length === 0
      ? 'unavailable'
      : priced.length === records.length && priced.every((r) => r.cost_basis === 'measured')
        ? 'measured'
        : 'partial'
  return {
    messages: records.length,
    turns: records.length,
    latency_ms: records.reduce((a, r) => a + (r.latency_ms ?? 0), 0),
    llm_calls: records.reduce((a, r) => a + (r.llm_calls ?? 0), 0),
    cost_usd: priced.length ? priced.reduce((a, r) => a + (r.cost_usd ?? 0), 0) : null,
    cost_basis,
  }
}

function fromTimeline(t: Timeline): ConversationState {
  const customer = t.customer
  const messages: ChatMessage[] = []
  let agentIndex = 0
  for (const m of t.messages) {
    // The opening greeting is saved by the backend as the agent's first
    // message; it isn't a turn, so it doesn't consume one.
    if (m.greeting) {
      messages.push({ id: m.id, role: 'greeting', content: m.content })
    } else if (m.role === 'customer') {
      messages.push({ id: m.id, role: 'customer', content: m.content })
    } else {
      const turn = t.turns[agentIndex++]
      messages.push({
        id: m.id,
        role: 'agent',
        content: m.content,
        turnIndex: turn?.index,
        latencyMs: turn?.latency_ms ?? null,
        llmCalls: turn?.llm_calls ?? null,
      })
    }
  }
  const turns: TurnView[] = t.turns.map((tt) => ({
    index: tt.index,
    customerMessage: tt.customer_message ?? '',
    record: tt,
    liveEvents: [],
    status: tt.error ? 'error' : 'done',
    queuePosition: 0,
    startedAt: 0,
  }))
  const status = t.conversation.status
  const phase: Phase = t.conversation.csat ? 'rated' : status === 'active' ? 'chatting' : 'ended'
  return {
    customer,
    status,
    messages,
    turns,
    phase,
    escalated: status === 'escalated',
    busy: false,
    error: null,
    totals: t.totals,
    checks: t.checks,
    csat: t.conversation.csat,
  }
}

export function useConversation(conversationId: string) {
  const [state, setState] = useState<ConversationState>(INITIAL)
  const closeRef = useRef<(() => void) | null>(null)
  const turnCountRef = useRef(0)

  useEffect(() => {
    let cancelled = false
    setState(INITIAL)
    api
      .timeline(conversationId)
      .then((t) => {
        if (cancelled) return
        const s = fromTimeline(t)
        turnCountRef.current = s.turns.length
        setState(s)
      })
      .catch((e) => {
        if (!cancelled) setState({ ...INITIAL, phase: 'missing', error: friendlyError(e) })
      })
    return () => {
      cancelled = true
      closeRef.current?.()
      closeRef.current = null
    }
  }, [conversationId])

  const patchTurn = useCallback((index: number, fn: (t: TurnView) => TurnView) => {
    setState((s) => ({ ...s, turns: s.turns.map((t) => (t.index === index ? fn(t) : t)) }))
  }, [])

  const patchAgentMessage = useCallback((id: string, fn: (m: ChatMessage) => ChatMessage) => {
    setState((s) => ({ ...s, messages: s.messages.map((m) => (m.id === id ? fn(m) : m)) }))
  }, [])

  const send = useCallback(
    async (text: string) => {
      const customer = state.customer
      const trimmed = text.trim()
      if (!customer || state.busy || !trimmed) return
      setState((s) => ({ ...s, error: null, busy: true }))
      try {
        await api.sendMessage(conversationId, customer.customer_id, trimmed)
      } catch (e) {
        setState((s) => ({ ...s, busy: false, error: friendlyError(e) }))
        return
      }
      const index = turnCountRef.current + 1
      turnCountRef.current = index
      const agentId = `a-${index}`
      setState((s) => ({
        ...s,
        messages: [
          ...s.messages,
          { id: `c-${index}`, role: 'customer', content: trimmed },
          { id: agentId, role: 'agent', content: '', turnIndex: index, streaming: true },
        ],
        turns: [
          ...s.turns,
          {
            index,
            customerMessage: trimmed,
            record: null,
            liveEvents: [],
            status: 'running',
            queuePosition: 0,
            startedAt: Date.now(),
          },
        ],
      }))

      closeRef.current = streamTurn(conversationId, {
        onQueued: (position) => patchTurn(index, (t) => ({ ...t, status: 'queued', queuePosition: position })),
        onStep: (step) => {
          const { index: _i, ...event } = step
          void _i
          patchTurn(index, (t) => ({
            ...t,
            status: 'running',
            queuePosition: 0,
            liveEvents: [...t.liveEvents, event as PipelineEvent],
          }))
        },
        onTurn: (record) =>
          patchTurn(index, (t) => ({ ...t, record, status: record.error ? 'error' : 'streaming', queuePosition: 0 })),
        onToken: (token) => patchAgentMessage(agentId, (m) => ({ ...m, content: m.content + token })),
        onTurnComplete: (record) =>
          setState((s) => {
            const turns = s.turns.map((t) =>
              t.index === index ? { ...t, record, status: (record.error ? 'error' : 'done') as TurnStatus } : t,
            )
            const records = turns.map((t) => t.record).filter((r): r is TurnRecord => r != null)
            return {
              ...s,
              turns,
              totals: totalsFrom(records),
              messages: s.messages.map((m) =>
                m.id === agentId ? { ...m, latencyMs: record.latency_ms, llmCalls: record.llm_calls } : m,
              ),
            }
          }),
        onEscalated: () => setState((s) => ({ ...s, escalated: true, status: 'escalated' })),
        onDone: () =>
          setState((s) => ({
            ...s,
            busy: false,
            phase: s.escalated ? 'ended' : s.phase,
            messages: s.messages.map((m) => (m.id === agentId ? { ...m, streaming: false } : m)),
            turns: s.turns.map((t) =>
              t.index === index && t.status !== 'done' && t.status !== 'error'
                ? { ...t, status: (t.record?.error ? 'error' : 'done') as TurnStatus }
                : t,
            ),
          })),
        onError: () =>
          setState((s) => ({
            ...s,
            busy: false,
            error: 'The connection dropped mid-turn. Reload the page to see where things landed.',
            messages: s.messages.map((m) => (m.id === agentId ? { ...m, streaming: false } : m)),
            turns: s.turns.map((t) => (t.index === index && !t.record ? { ...t, status: 'error' } : t)),
          })),
      })
    },
    [conversationId, state.customer, state.busy, patchTurn, patchAgentMessage],
  )

  const endChat = useCallback(async () => {
    closeRef.current?.()
    closeRef.current = null
    try {
      const r = await api.endConversation(conversationId)
      setState((s) => ({ ...s, phase: 'ended', busy: false, status: r.status as ConversationState['status'] }))
    } catch (e) {
      setState((s) => ({ ...s, error: friendlyError(e) }))
    }
  }, [conversationId])

  const submitCsat = useCallback(
    async (rating: number, tags: string[], free_text?: string) => {
      await api.submitCsat({ conversation_id: conversationId, rating, tags, free_text: free_text || undefined })
      const t = await api.timeline(conversationId)
      setState((s) => ({ ...s, csat: t.conversation.csat, checks: t.checks, totals: t.totals, phase: 'rated' }))
    },
    [conversationId],
  )

  const clearError = useCallback(() => setState((s) => ({ ...s, error: null })), [])

  return { ...state, conversationId, send, endChat, submitCsat, clearError }
}

/** Seconds elapsed since `startedAt`, ticking while `active`. */
export function useElapsed(startedAt: number, active: boolean): number {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(id)
  }, [active])
  if (!startedAt) return 0
  return Math.max(0, Math.round((now - startedAt) / 1000))
}
