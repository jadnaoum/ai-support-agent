import type { OrderStatus } from './types'

export const statusChip: Record<OrderStatus, string> = {
  delivered: 'bg-brand-green-soft text-brand-green-dark',
  shipped: 'bg-blue-100 text-blue-700',
  processing: 'bg-amber-100 text-amber-700',
  cancelled: 'bg-red-100 text-red-700',
  returned: 'bg-[#ece9f7] text-[#6b5fb8]',
}

export const statusLabel: Record<OrderStatus, string> = {
  delivered: 'Delivered',
  shipped: 'Shipped',
  processing: 'Processing',
  cancelled: 'Cancelled',
  returned: 'Returned',
}
