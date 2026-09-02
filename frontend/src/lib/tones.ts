import type { OrderStatus } from './types'

/**
 * Colour logic for the whole demo, in one place:
 *   green  = passed / succeeded, nothing to see
 *   purple = the system intervened (blocked input, flagged response,
 *            confirmation required, handed to a human)
 *   amber  = contextual, not broken (ineligible, no KB match)
 *   blue   = knowledge lookup
 *   red    = infrastructure error
 *   grey   = nothing happened
 *   indigo = running right now
 */
export type Tone = 'green' | 'purple' | 'amber' | 'blue' | 'red' | 'grey' | 'indigo'

export const toneIcon: Record<Tone, string> = {
  green: 'bg-brand-green-soft text-brand-green-dark',
  purple: 'bg-brand-purple text-white',
  amber: 'bg-amber-100 text-amber-700',
  blue: 'bg-blue-100 text-blue-700',
  red: 'bg-red-100 text-red-700',
  grey: 'bg-neutral-100 text-neutral-400',
  indigo: 'bg-indigo-100 text-indigo-700',
}

export const toneText: Record<Tone, string> = {
  green: 'text-neutral-700',
  purple: 'text-brand-purple-dark',
  amber: 'text-neutral-700',
  blue: 'text-neutral-700',
  red: 'text-red-700',
  grey: 'text-neutral-400',
  indigo: 'text-indigo-700',
}

/** Row highlight for "system intervened" steps. */
export const toneRow: Partial<Record<Tone, string>> = {
  purple: 'bg-brand-purple-soft -mx-2 px-2 rounded-md',
  red: 'bg-red-50 -mx-2 px-2 rounded-md',
}

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
