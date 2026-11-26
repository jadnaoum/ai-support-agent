import { cn } from '@/lib/utils'

export function SectionLabel({
  tone,
  size = 'sm',
  children,
}: {
  tone: 'green' | 'purple'
  size?: 'sm' | 'lg'
  children: string
}) {
  const color = tone === 'green' ? 'bg-brand-green text-brand-green' : 'bg-brand-purple text-brand-purple'
  return (
    <div className="flex items-center gap-2">
      <span className={cn('inline-block rounded-full', size === 'lg' ? 'h-2.5 w-2.5' : 'h-2 w-2', color.split(' ')[0])} />
      <span
        className={cn(
          'uppercase',
          size === 'lg' ? 'text-lg font-bold tracking-wide' : 'text-[13px] font-semibold tracking-wider',
          color.split(' ')[1],
        )}
      >
        {children}
      </span>
    </div>
  )
}
