import { useEffect, useState } from 'react'
import { ChevronDown, X } from 'lucide-react'
import { api } from '@/lib/api'
import type { Rules } from '@/lib/types'
import { cn } from '@/lib/utils'

function RuleLine({ text, source }: { text: string; source: string }) {
  return (
    <div className="flex gap-2 text-[13px] leading-snug text-neutral-600" title={`Derived from ${source}`}>
      <span className="text-neutral-400">—</span>
      <span>{text}</span>
    </div>
  )
}

export function RulesPanel({ customerId }: { customerId: string }) {
  const [rules, setRules] = useState<Rules | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .rules(customerId)
      .then((r) => !cancelled && setRules(r))
      .catch(() => !cancelled && setRules({ account: [], global: [] }))
    return () => {
      cancelled = true
    }
  }, [customerId])

  const all = rules ? [...rules.account, ...rules.global] : []

  return (
    <div className="flex flex-col items-end gap-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex items-center gap-1 text-[13px] text-brand-purple-dark underline-offset-2 hover:underline"
      >
        See the policies for this order
        <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="flex w-full flex-col gap-2 rounded-[10px] border border-neutral-200 bg-white px-4 py-3.5 shadow-lg animate-in fade-in slide-in-from-top-1">
          <div className="flex items-center justify-between">
            <span className="text-[13px] font-semibold text-neutral-900">Policies for this order</span>
            <button
              type="button"
              aria-label="Close policies"
              onClick={() => setOpen(false)}
              className="rounded p-0.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="flex flex-col gap-1.5">
            {rules === null && <span className="text-xs text-neutral-400">Loading…</span>}
            {all.map((r) => (
              <RuleLine key={r.product_id ?? r.text} text={r.text} source={r.source} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
