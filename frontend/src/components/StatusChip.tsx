import type { OrderStatus } from '@/lib/types'
import { statusChip, statusLabel } from '@/lib/tones'
import { cn } from '@/lib/utils'

export function StatusChip({ status }: { status: OrderStatus }) {
  return (
    <span className={cn('shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold', statusChip[status])}>
      {statusLabel[status] ?? status}
    </span>
  )
}
