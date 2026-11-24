import { ArrowRight, Loader2 } from 'lucide-react'
import type { Scenario } from '@/lib/types'
import { StatusChip } from '@/components/StatusChip'
import { cn } from '@/lib/utils'

export function ScenarioCard({
  name,
  scenario,
  starting,
  disabled,
  onStart,
}: {
  name: string
  scenario: Scenario
  starting: boolean
  disabled: boolean
  onStart: () => void
}) {
  return (
    <button
      type="button"
      onClick={onStart}
      disabled={disabled}
      className={cn(
        'flex flex-col gap-3.5 rounded-xl border border-neutral-200 bg-white p-5 text-left shadow-[0_1px_2px_rgba(0,0,0,0.03)] transition-all',
        'hover:border-brand-green hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-green',
        'disabled:cursor-default disabled:opacity-70',
      )}
    >
      <span className="text-xl font-semibold text-neutral-900">{name}</span>
      <div className="flex flex-col gap-2">
        {scenario.orders.map((o, i) => (
          <div key={i} className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-[13px] text-neutral-800">{o.product_name}</span>
              <span className="text-xs text-neutral-400">
                {o.status === 'delivered' ? 'delivered' : 'ordered'} {o.ordered_label}
              </span>
            </div>
            <StatusChip status={o.status} />
          </div>
        ))}
      </div>
      <p className="border-t border-neutral-100 pt-3 text-[13px] italic leading-snug text-neutral-600">“{scenario.dare}”</p>
      <span className="mt-0.5 flex items-center gap-1.5 text-[13px] font-semibold text-brand-green">
        {starting ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Setting up {name}…
          </>
        ) : (
          <>
            Start as {name} <ArrowRight className="h-3.5 w-3.5" />
          </>
        )}
      </span>
    </button>
  )
}
