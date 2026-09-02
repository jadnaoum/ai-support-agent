import { useEffect, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { api } from '@/lib/api'
import type { Rules } from '@/lib/types'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
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

  return (
    <div className="flex flex-col gap-2 rounded-[10px] border border-neutral-200 bg-white px-4 py-3.5">
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-semibold text-neutral-900">Rules that apply to this account</span>
      </div>
      <div className="flex flex-col gap-1.5">
        {rules === null && <span className="text-xs text-neutral-400">Loading…</span>}
        {rules?.account.map((r) => (
          <RuleLine key={r.product_id ?? r.text} text={r.text} source={r.source} />
        ))}
        {rules?.global.slice(0, 3).map((r) => (
          <RuleLine key={r.text} text={r.text} source={r.source} />
        ))}
      </div>
      {rules && rules.global.length > 3 && (
        <Collapsible open={open} onOpenChange={setOpen}>
          <CollapsibleTrigger className="flex items-center gap-1 text-xs text-brand-purple-dark hover:underline">
            {open ? 'Fewer rules' : 'All rules'}
            <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')} />
          </CollapsibleTrigger>
          <CollapsibleContent className="flex flex-col gap-1.5 pt-1.5">
            {rules.global.slice(3).map((r) => (
              <RuleLine key={r.text} text={r.text} source={r.source} />
            ))}
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  )
}
