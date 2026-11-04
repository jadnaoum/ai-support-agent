import { useState } from 'react'
import { AlertCircle, Check, Link2, Minus } from 'lucide-react'
import type { Check as CheckResult, Totals } from '@/lib/types'
import { fmtCost, fmtLatency } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { SectionLabel } from '@/components/SectionLabel'
import { cn } from '@/lib/utils'

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-lg bg-neutral-50 px-3.5 py-3">
      <span className="text-[11px] text-neutral-400">{label}</span>
      <span className="text-xl font-semibold text-neutral-900">{value}</span>
    </div>
  )
}

function CheckRow({ c }: { c: CheckResult }) {
  const icon =
    c.status === 'pass' ? (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-green-soft text-brand-green-dark">
        <Check className="h-3 w-3" strokeWidth={2.5} />
      </span>
    ) : c.status === 'fail' ? (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-amber-100 text-amber-700">
        <AlertCircle className="h-3 w-3" strokeWidth={2.5} />
      </span>
    ) : (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-neutral-100 text-neutral-400">
        <Minus className="h-3 w-3" strokeWidth={2.5} />
      </span>
    )
  return (
    <div className={cn('flex items-center gap-2.5 text-[13px]', c.status === 'na' ? 'text-neutral-400' : 'text-neutral-700')}>
      {icon}
      {c.text}
    </div>
  )
}

export function SummaryCard({
  totals,
  checks,
  customerName,
  conversationId,
  readOnly,
  onNewChat,
  onNewScenario,
}: {
  totals: Totals
  checks: CheckResult[]
  customerName: string
  conversationId: string
  readOnly: boolean
  onNewChat: () => void
  onNewScenario: () => void
}) {
  const [copied, setCopied] = useState(false)

  async function copyLink() {
    const url = `${window.location.origin}/replay/${conversationId}`
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      window.prompt('Copy this link', url)
    }
  }

  return (
    <Card className="flex flex-col gap-[18px] p-5">
      <SectionLabel tone="green">How it went</SectionLabel>
      <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
        <Tile label="Messages" value={String(totals.messages)} />
        <Tile label="Total latency" value={fmtLatency(totals.latency_ms)} />
        <Tile label="Total cost" value={fmtCost(totals.cost_usd, totals.cost_basis)} />
        <Tile label="LLM calls" value={String(totals.llm_calls)} />
      </div>
      <div className="flex flex-col gap-2">
        {checks.map((c) => (
          <CheckRow key={c.id} c={c} />
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2.5 pt-1">
        {readOnly ? (
          <Button onClick={onNewScenario}>Try it yourself</Button>
        ) : (
          <>
            <Button onClick={onNewChat}>New chat as {customerName}</Button>
            <Button variant="outline" onClick={onNewScenario}>
              New scenario
            </Button>
          </>
        )}
        <button
          type="button"
          onClick={copyLink}
          className="ml-auto flex items-center gap-1.5 text-[13px] text-brand-purple-dark hover:underline"
        >
          <Link2 className="h-3.5 w-3.5" />
          {copied ? 'Copied' : 'Copy replay link'}
        </button>
      </div>
    </Card>
  )
}
