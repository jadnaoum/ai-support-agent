import type { CostBasis } from './types'

export function fmtLatency(ms: number | null | undefined): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function fmtCost(usd: number | null | undefined, basis?: CostBasis): string {
  if (usd == null || basis === 'unavailable') return '—'
  const s = usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(3)}`
  return basis === 'partial' ? `${s}+` : s
}

export function fmtCount(n: number | null | undefined): string {
  return n == null ? '—' : String(n)
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return ''
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)}s`
}

export function truncate(s: string, n = 48): string {
  return s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s
}

export function humanize(s: string | null | undefined): string {
  return (s || '').replace(/_/g, ' ')
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}
