import { AlertCircle, BookOpen, Check, Loader2, Minus, Shield, UserCheck, Wrench, XCircle } from 'lucide-react'
import type { StepView } from '@/lib/pipeline'
import { toneIcon, toneRow, toneText } from '@/lib/tones'
import { cn } from '@/lib/utils'

const ICONS = {
  check: Check,
  shield: Shield,
  tool: Wrench,
  book: BookOpen,
  alert: AlertCircle,
  error: XCircle,
  handoff: UserCheck,
  minus: Minus,
  spinner: Loader2,
} as const

export function PipelineStep({ step, pulse }: { step: StepView; pulse?: boolean }) {
  const Icon = ICONS[step.icon]
  return (
    <div
      className={cn(
        'flex items-center justify-between gap-2.5 py-1',
        toneRow[step.tone],
        pulse && 'animate-soft-pulse',
      )}
    >
      <div className="flex min-w-0 items-center gap-2.5">
        <span className={cn('flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full', toneIcon[step.tone])}>
          <Icon className={cn('h-[13px] w-[13px]', step.icon === 'spinner' && 'animate-spin')} strokeWidth={step.icon === 'check' ? 2.5 : 2} />
        </span>
        <span className={cn('min-w-0 text-[13px] leading-snug', toneText[step.tone])}>
          {step.code && (
            <code className="mr-1 rounded bg-neutral-100 px-1.5 py-px font-mono text-xs text-neutral-800">{step.code}</code>
          )}
          {step.label}
          {step.detail && <span className={cn('ml-1', step.tone === 'purple' ? 'text-brand-purple-dark/70' : 'text-neutral-400')}>· {step.detail}</span>}
        </span>
      </div>
      {step.right && <span className="shrink-0 font-mono text-[11px] text-neutral-400">{step.right}</span>}
    </div>
  )
}
