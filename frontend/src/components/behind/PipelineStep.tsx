import { AlertCircle, BookOpen, Check, Clock, Package, PenLine, Send, Shield, ShieldCheck, Sparkles, X } from 'lucide-react'
import type { StepKind, StepStatus, StepView } from '@/lib/pipeline'
import { cn } from '@/lib/utils'

const KIND_ICON: Record<StepKind, typeof Shield> = {
  safety: Shield,
  tool: Package,
  kb: BookOpen,
  decide: Sparkles,
  write: PenLine,
  verify: ShieldCheck,
  send: Send,
  wait: Clock,
  error: AlertCircle,
}

function StatusMark({ status }: { status: StepStatus }) {
  switch (status) {
    case 'pending':
      return <span aria-label="Not reached yet" className="m-[3px] h-3 w-3 rounded-full border-2 border-dashed border-neutral-300" />
    case 'running':
      return (
        <span
          aria-label="Running"
          className="m-0.5 h-3.5 w-3.5 animate-spin rounded-full border-2 border-neutral-200 border-t-brand-purple"
        />
      )
    case 'done':
      return (
        <span aria-label="Done" className="flex h-[18px] w-[18px] items-center justify-center rounded-full bg-brand-green text-white">
          <Check className="h-[11px] w-[11px]" strokeWidth={3.5} />
        </span>
      )
    case 'stopped':
      return (
        <span aria-label="A rule stepped in" className="flex h-[18px] w-[18px] items-center justify-center rounded-full bg-brand-purple text-[11px] font-bold leading-none text-white">
          !
        </span>
      )
    case 'error':
      return (
        <span aria-label="Error" className="flex h-[18px] w-[18px] items-center justify-center rounded-full bg-red-600 text-white">
          <X className="h-[11px] w-[11px]" strokeWidth={3.5} />
        </span>
      )
  }
}

export function PipelineStep({ step }: { step: StepView }) {
  const Icon = KIND_ICON[step.kind]
  const pending = step.status === 'pending'
  return (
    <div
      className={cn(
        'flex items-center gap-3 py-1.5',
        step.status === 'running' && '-mx-2 rounded-lg bg-neutral-50 px-2',
      )}
    >
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-neutral-100 text-neutral-600">
        <Icon className={cn('h-[15px] w-[15px]', pending && 'text-neutral-400')} strokeWidth={2} />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-px">
        <span className={cn('text-[13px] leading-snug', pending ? 'text-neutral-400' : 'text-neutral-800')}>{step.title}</span>
        {(step.code || step.detail) && (
          <span
            className={cn(
              'text-xs leading-snug',
              step.status === 'stopped' ? 'text-brand-purple-dark' : step.status === 'error' ? 'text-red-700' : 'text-neutral-500',
            )}
          >
            {step.code && <span className="font-mono text-[11px]">{step.code}</span>}
            {step.code && step.detail && ' · '}
            {step.detail}
          </span>
        )}
      </div>
      <span className="flex shrink-0 items-center gap-2">
        {step.right && <span className="font-mono text-[11px] text-neutral-400">{step.right}</span>}
        <StatusMark status={step.status} />
      </span>
    </div>
  )
}
