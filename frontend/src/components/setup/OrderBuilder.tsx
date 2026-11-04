import { Plus, X } from 'lucide-react'
import type { OrderSpec, OrderStatus, Product } from '@/lib/types'
import { statusLabel } from '@/lib/tones'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

const EMPTY: OrderSpec = { product_id: '', status: '', ordered_relative: '' }

export function OrderBuilder({
  products,
  statuses,
  relativeDates,
  maxOrders,
  rows,
  onChange,
}: {
  products: Product[]
  statuses: OrderStatus[]
  relativeDates: { id: string; label: string }[]
  maxOrders: number
  rows: OrderSpec[]
  onChange: (rows: OrderSpec[]) => void
}) {
  function patch(i: number, p: Partial<OrderSpec>) {
    onChange(rows.map((r, j) => (j === i ? { ...r, ...p } : r)))
  }

  return (
    <div className="flex flex-col gap-2.5">
      {rows.map((row, i) => (
        <div key={i} className="grid grid-cols-1 items-center gap-2.5 md:grid-cols-[1fr_1fr_1fr_auto]">
          <Select value={row.product_id} onValueChange={(v) => patch(i, { product_id: v })}>
            <SelectTrigger aria-label="Product">
              <SelectValue placeholder="Product" />
            </SelectTrigger>
            <SelectContent>
              {products.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={row.status} onValueChange={(v) => patch(i, { status: v as OrderStatus })}>
            <SelectTrigger aria-label="Status">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              {statuses.map((s) => (
                <SelectItem key={s} value={s}>
                  {statusLabel[s] ?? s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={row.ordered_relative} onValueChange={(v) => patch(i, { ordered_relative: v })}>
            <SelectTrigger aria-label="Ordered when">
              <SelectValue placeholder="Ordered when" />
            </SelectTrigger>
            <SelectContent>
              {relativeDates.map((d) => (
                <SelectItem key={d.id} value={d.id}>
                  {d.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <button
            type="button"
            aria-label="Remove order"
            onClick={() => onChange(rows.filter((_, j) => j !== i))}
            disabled={rows.length === 1}
            className="flex h-10 w-10 items-center justify-center rounded-lg text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700 disabled:invisible"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      ))}
      {rows.length < maxOrders && (
        <button
          type="button"
          onClick={() => onChange([...rows, EMPTY])}
          className="mt-1 flex w-fit items-center gap-1.5 text-[13px] text-brand-purple-dark hover:underline"
        >
          <Plus className="h-3.5 w-3.5" />
          Add another order <span className="text-neutral-400">(up to {maxOrders})</span>
        </button>
      )}
    </div>
  )
}
