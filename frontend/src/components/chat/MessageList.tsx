import { useEffect, useRef } from 'react'
import { Loader2, UserCheck } from 'lucide-react'
import type { ChatMessage, TurnView } from '@/hooks/useConversation'
import { useElapsed } from '@/hooks/useConversation'
import { fmtLatency } from '@/lib/format'
import { nextStepLabel } from '@/components/behind/TurnCard'

function Avatar() {
  return (
    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-neutral-900 text-xs font-semibold text-white">
      A
    </div>
  )
}

function Placeholder({ turn }: { turn: TurnView | undefined }) {
  const active = !!turn && (turn.status === 'running' || turn.status === 'queued' || turn.status === 'streaming')
  const elapsed = useElapsed(turn?.startedAt ?? 0, active)
  let text = 'Working on it'
  if (turn?.status === 'queued')
    text = `Waiting for a free slot · ${turn.queuePosition === 1 ? 'you’re next' : `${turn.queuePosition - 1} ahead of you`}`
  else {
    const stage = turn ? nextStepLabel(turn.liveEvents) : 'Working on it'
    text = elapsed > 0 ? `${stage} · ${elapsed}s` : stage
  }
  return (
    <div className="flex animate-soft-pulse items-center gap-2 rounded-2xl rounded-bl-[4px] border border-dashed border-neutral-200 bg-neutral-50 px-3.5 py-2.5 text-[13px] text-neutral-500">
      <Loader2 className="h-3.5 w-3.5 animate-spin" />
      {text}
    </div>
  )
}

export function MessageList({
  messages,
  turns,
  escalated,
  ended,
}: {
  messages: ChatMessage[]
  turns: TurnView[]
  escalated: boolean
  ended: boolean
}) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const lastContent = messages[messages.length - 1]?.content
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, lastContent, ended])

  return (
    <div className="flex max-h-[55vh] flex-col gap-4 overflow-y-auto px-6 pb-4 pt-1 lg:max-h-none lg:min-h-0 lg:flex-1">
      {messages.map((m) => {
        if (m.role === 'customer') {
          return (
            <div key={m.id} className="flex justify-end">
              <div className="max-w-[480px] whitespace-pre-wrap rounded-2xl rounded-br-[4px] bg-brand-purple-soft px-3.5 py-2.5 text-sm leading-relaxed text-brand-purple-dark">
                {m.content}
              </div>
            </div>
          )
        }
        const turn = m.turnIndex != null ? turns.find((t) => t.index === m.turnIndex) : undefined
        const caption =
          m.role === 'greeting'
            ? 'Greeting · no pipeline'
            : m.streaming && !m.content
              ? null
              : `Agent${m.latencyMs != null ? ` · ${fmtLatency(m.latencyMs)}` : ''}${
                  m.llmCalls != null ? ` · ${m.llmCalls} ${m.llmCalls === 1 ? 'call' : 'calls'}` : ''
                }`
        return (
          <div key={m.id} className="flex items-start gap-2.5">
            <Avatar />
            <div className="flex max-w-[520px] flex-col gap-1">
              {m.streaming && !m.content ? (
                <Placeholder turn={turn} />
              ) : (
                <div className="whitespace-pre-wrap rounded-2xl rounded-bl-[4px] bg-neutral-100 px-3.5 py-2.5 text-sm leading-relaxed text-neutral-800">
                  {m.content}
                  {m.streaming && <span className="text-neutral-400">▋</span>}
                </div>
              )}
              {caption && <span className="pl-1 text-[11px] text-neutral-400">{caption}</span>}
            </div>
          </div>
        )
      })}
      {escalated && (
        <div className="flex items-center gap-2 rounded-lg bg-brand-purple-soft px-3.5 py-2.5 text-[13px] text-brand-purple-dark">
          <UserCheck className="h-4 w-4" />
          This conversation is with a specialist now. The agent won’t act on it again.
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}
