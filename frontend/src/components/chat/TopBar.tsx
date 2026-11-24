import type { DemoCustomer, Totals } from '@/lib/types'
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
  totals,
  escalated,
  ended,
  readOnly,
  busy,
  onEnd,
}: {
  customer: DemoCustomer
  totals: Totals
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
        <Pill label="Messages" value={String(totals.messages)} />
        <Pill label="Latency" value={fmtLatency(totals.latency_ms)} />
        <Pill label="Cost" value={fmtCost(totals.cost_usd, totals.cost_basis)} />
        <Pill label="LLM calls" value={String(totals.llm_calls)} />
        {!readOnly && (
          <Button variant="outline" size="sm" className="ml-2 shrink-0" onClick={onEnd} disabled={ended || busy}>
            {ended ? 'Chat ended' : 'End chat'}
          </Button>
        )}
      </div>
    </header>
  )
}
