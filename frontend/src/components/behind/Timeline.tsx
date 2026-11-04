import type { TurnView } from '@/hooks/useConversation'
import { TurnCard } from './TurnCard'

export function Timeline({ turns }: { turns: TurnView[] }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between rounded-[10px] border border-dashed border-neutral-200 px-4 py-2.5 text-[13px] text-neutral-400">
        <span>Greeting</span>
        <span className="text-xs">static · no pipeline</span>
      </div>
      {turns.map((t) => (
        <TurnCard key={t.index} turn={t} />
      ))}
    </div>
  )
}
