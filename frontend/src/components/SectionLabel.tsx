import { cn } from '@/lib/utils'

export function SectionLabel({ tone, children }: { tone: 'green' | 'purple'; children: string }) {
  const color = tone === 'green' ? 'bg-brand-green text-brand-green' : 'bg-brand-purple text-brand-purple'
  return (
    <div className="flex items-center gap-2">
      <span className={cn('inline-block h-2 w-2 rounded-full', color.split(' ')[0])} />
      <span className={cn('text-[13px] font-semibold uppercase tracking-wider', color.split(' ')[1])}>{children}</span>
    </div>
  )
}
