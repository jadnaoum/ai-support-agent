import type { StepEvent, TurnRecord } from '@/lib/types'

export interface StreamHandlers {
  onQueued?: (position: number) => void
  onStep?: (step: StepEvent) => void
  onTurn?: (record: TurnRecord) => void
  onToken?: (token: string) => void
  onTurnComplete?: (record: TurnRecord) => void
  onEscalated?: () => void
  onDone?: () => void
  onError?: () => void
}

/**
 * Open the SSE stream for the turn the customer just sent.
 * Event order from the backend: [queued] → step… → turn → token… → turn_complete → [escalated] → done.
 * Returns a function that closes the stream.
 */
export function streamTurn(conversationId: string, h: StreamHandlers): () => void {
  const es = new EventSource(`/api/chat/stream/${conversationId}`)
  let finished = false

  const parse = <T,>(e: MessageEvent): T | null => {
    try {
      return JSON.parse(e.data) as T
    } catch {
      return null
    }
  }

  es.addEventListener('queued', (e) => {
    const p = parse<{ position: number }>(e as MessageEvent)
    if (p) h.onQueued?.(p.position)
  })
  es.addEventListener('step', (e) => {
    const st = parse<StepEvent>(e as MessageEvent)
    if (st) h.onStep?.(st)
  })
  es.addEventListener('turn', (e) => {
    const r = parse<TurnRecord>(e as MessageEvent)
    if (r) h.onTurn?.(r)
  })
  es.addEventListener('token', (e) => h.onToken?.((e as MessageEvent).data))
  es.addEventListener('turn_complete', (e) => {
    const r = parse<TurnRecord>(e as MessageEvent)
    if (r) h.onTurnComplete?.(r)
  })
  es.addEventListener('escalated', () => h.onEscalated?.())
  es.addEventListener('done', () => {
    finished = true
    es.close()
    h.onDone?.()
  })
  es.addEventListener('error', () => {
    // EventSource would auto-reconnect and re-run the turn; never allow that.
    es.close()
    if (!finished) h.onError?.()
  })

  return () => es.close()
}
