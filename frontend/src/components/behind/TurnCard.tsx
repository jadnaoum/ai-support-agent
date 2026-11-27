import { UserCheck } from 'lucide-react'
import type { TurnView } from '@/hooks/useConversation'
import { useElapsed } from '@/hooks/useConversation'
import { turnSteps } from '@/lib/pipeline'
import { fmtLatency, humanize, truncate } from '@/lib/format'
import { PipelineStep } from './PipelineStep'

function Briefing({ reason, summary }: { reason: string; summary: string }) {
  const lines = summary.split('\n').filter(Boolean)
  return (
    <div className="mt-1 flex flex-col gap-3 rounded-lg border border-brand-purple-line bg-white p-3.5">
      <div className="flex items-center gap-2.5">
        <span className="flex h-[22px] w-[22px] items-center justify-center rounded-full bg-brand-purple text-white">
          <UserCheck className="h-[13px] w-[13px]" />
        </span>
        <div className="flex flex-col">
          <span className="text-[13px] font-semibold text-brand-purple-dark">Handed to a human</span>
          <span className="text-xs text-neutral-500">This is the briefing the specialist receives</span>
        </div>
      </div>
      <div className="flex flex-col gap-0.5 rounded-md bg-neutral-50 px-3.5 py-3 font-mono text-xs leading-relaxed text-neutral-700">
        <span>
          <span className="text-neutral-400">Reason: </span>
          {humanize(reason)}
        </span>
        {lines.map((l, i) => (
          <span key={i}>{l}</span>
        ))}
      </div>
    </div>
  )
}

export function TurnCard({ turn }: { turn: TurnView }) {
  const running = turn.status === 'running' || turn.status === 'queued' || turn.status === 'streaming'
  const elapsed = useElapsed(turn.startedAt, running)
  const record = turn.record
  const escalation = record?.events.find((e) => e.type === 'escalation')

  let right: string
  if (turn.status === 'queued') right = `queued · ${turn.queuePosition === 1 ? 'next' : `${turn.queuePosition - 1} ahead`}`
  else if (running && !record) right = elapsed ? `${elapsed}s…` : 'running…'
  else right = fmtLatency(record?.latency_ms)

  return (
    <div className={'flex flex-col gap-2 rounded-[10px] border bg-white px-4 py-3 ' + (running ? 'border-dashed border-brand-purple-line' : 'border-neutral-200')}>
      <div className="flex items-center justify-between gap-2.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-md bg-neutral-900 text-[11px] font-semibold text-white">
            {turn.index}
          </span>
          <span className="truncate text-[13px] text-neutral-700">“{truncate(turn.customerMessage, 56)}”</span>
        </div>
        <span className={'shrink-0 text-xs text-neutral-400 ' + (running ? 'animate-soft-pulse' : '')}>{right}</span>
      </div>
      <div className="flex flex-col border-t border-neutral-100 pt-1">
        {record ? (
          turnSteps(record.events, false).map((s, i) => <PipelineStep key={i} step={s} />)
        ) : turn.status === 'queued' ? (
          <PipelineStep step={{ kind: 'wait', status: 'pending', title: 'Waiting for a free slot' }} />
        ) : turn.status === 'error' ? (
          <PipelineStep step={{ kind: 'error', status: 'error', title: 'The stream dropped before the turn finished' }} />
        ) : (
          turnSteps(turn.liveEvents, true).map((s, i) => <PipelineStep key={i} step={s} />)
        )}
        {escalation && escalation.type === 'escalation' && (
          <Briefing reason={escalation.reason} summary={escalation.context_summary} />
        )}
      </div>
    </div>
  )
}
