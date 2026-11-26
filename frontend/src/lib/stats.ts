import type { CostBasis, TurnRecord } from './types'

export interface ReplyStats {
  lastLatencyMs: number | null
  lastCostUsd: number | null
  lastCostBasis: CostBasis
  avgLatencyMs: number | null
  maxLatencyMs: number | null
}

/** Per-reply figures over finished turns (the running turn has no record yet). */
export function replyStats(records: (TurnRecord | null)[]): ReplyStats {
  const done = records.filter((r): r is TurnRecord => r != null)
  const last = done[done.length - 1]
  const latencies = done.map((r) => r.latency_ms).filter((ms): ms is number => ms != null)
  return {
    lastLatencyMs: last?.latency_ms ?? null,
    lastCostUsd: last?.cost_usd ?? null,
    lastCostBasis: last?.cost_basis ?? 'unavailable',
    avgLatencyMs: latencies.length ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length) : null,
    maxLatencyMs: latencies.length ? Math.max(...latencies) : null,
  }
}
