import { Link } from 'react-router-dom'
import type { DemoCustomer } from '@/lib/types'
import type { ReplyStats } from '@/lib/stats'
import { fmtCost, fmtLatency } from '@/lib/format'
import { Button } from '@/components/ui/button'

function Pill({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-600">
      <span className="text-neutral-400">{label}</span>
      <span className="font-semibold text-neutral-800">{value}</span>
    </div>
  )
}

export function TopBar({
  customer,
  stats,
  escalated,
  ended,
  readOnly,
  busy,
  onEnd,
}: {
  customer: DemoCustomer
  stats: ReplyStats
  escalated: boolean
  ended: boolean
  readOnly: boolean
  busy: boolean
  onEnd: () => void
}) {
  const n = customer.orders.length
  return (
    <header className="flex min-h-14 shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-neutral-200 px-4 py-2 md:px-6">
      <div className="flex items-center gap-2.5 text-sm">
        <span className="text-[15px] font-semibold text-neutral-900">{customer.name}</span>
        <span className="text-neutral-300">·</span>
        <span className="text-neutral-500">
          {n} {n === 1 ? 'order' : 'orders'}
        </span>
        {escalated && (
          <span className="rounded-full bg-brand-purple-soft px-2 py-0.5 text-xs font-medium text-brand-purple-dark">
            Handed to a human
          </span>
        )}
        {readOnly && (
          <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-500">Replay</span>
        )}
      </div>
      <div className="flex max-w-full items-center gap-2 overflow-x-auto">
        <Pill label="Last reply time" value={fmtLatency(stats.lastLatencyMs)} />
        <Pill label="Last reply cost" value={fmtCost(stats.lastCostUsd, stats.lastCostBasis)} />
        {!readOnly &&
          (ended ? (
            <Button asChild size="sm" className="ml-2 shrink-0">
              <Link to="/">Start over</Link>
            </Button>
          ) : (
            <Button variant="outline" size="sm" className="ml-2 shrink-0" onClick={onEnd} disabled={busy}>
              End chat
            </Button>
          ))}
      </div>
    </header>
  )
}
