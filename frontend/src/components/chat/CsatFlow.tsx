import { useState } from 'react'
import { Check, Star } from 'lucide-react'
import type { Csat } from '@/lib/types'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

export const NEGATIVE_TAGS: [string, string][] = [
  ['made_things_up', 'Agent made things up'],
  ['didnt_understand', "Agent didn't understand me"],
  ['going_in_circles', 'Agent kept going in circles'],
  ['sounded_robotic', 'Agent sounded robotic'],
  ['too_slow', 'Agent was too slow'],
]
export const POSITIVE_TAGS: [string, string][] = [
  ['quick_and_easy', 'Quick and easy'],
  ['agent_understood_me', 'Agent understood me right away'],
  ['accurate_information', 'Got accurate information'],
  ['felt_like_real_person', 'Felt like talking to a real person'],
  ['handled_tricky_situation', 'Handled a tricky situation well'],
]
const TAG_LABEL = Object.fromEntries([...NEGATIVE_TAGS, ...POSITIVE_TAGS])

function Stars({ value, size = 'lg', onPick }: { value: number; size?: 'sm' | 'lg'; onPick?: (n: number) => void }) {
  const [hover, setHover] = useState(0)
  const dim = size === 'lg' ? 'h-7 w-7' : 'h-4 w-4'
  return (
    <div className="flex gap-0.5" onMouseLeave={() => setHover(0)}>
      {[1, 2, 3, 4, 5].map((n) => {
        const lit = n <= (hover || value)
        return (
          <button
            key={n}
            type="button"
            disabled={!onPick}
            aria-label={`${n} star${n === 1 ? '' : 's'}`}
            onMouseEnter={() => onPick && setHover(n)}
            onClick={() => onPick?.(n)}
            className={cn('transition-transform', onPick && 'hover:scale-110')}
          >
            <Star
              className={cn(dim, lit ? 'fill-amber-400 text-amber-400' : 'fill-transparent text-neutral-300')}
              strokeWidth={1.5}
            />
          </button>
        )
      })}
    </div>
  )
}

export function CsatFlow({ onSubmit }: { onSubmit: (rating: number, tags: string[], text?: string) => Promise<void> }) {
  const [rating, setRating] = useState(0)
  const [tags, setTags] = useState<string[]>([])
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const band = rating >= 4 ? POSITIVE_TAGS : NEGATIVE_TAGS

  function pick(n: number) {
    if (n !== rating) setTags([])
    setRating(n)
  }

  async function submit() {
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit(rating, tags, text.trim() || undefined)
    } catch {
      setError('Could not save your feedback. Try again.')
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-neutral-800">How was your experience?</span>
        <Stars value={rating} onPick={pick} />
        <div className="flex w-[152px] justify-between text-[11px] text-neutral-400">
          <span>Completely lost</span>
          <span>Nailed it</span>
        </div>
      </div>

      {rating > 0 && (
        <div className="flex flex-col gap-3 animate-in fade-in slide-in-from-top-1">
          <span className="text-sm font-medium text-neutral-800">{rating >= 4 ? 'What went well?' : 'What went wrong?'}</span>
          <div className="flex flex-wrap gap-2">
            {band.map(([id, label]) => {
              const on = tags.includes(id)
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => setTags((t) => (on ? t.filter((x) => x !== id) : [...t, id]))}
                  className={cn(
                    'rounded-full border px-3 py-1 text-[13px] transition-colors',
                    on
                      ? 'border-brand-purple bg-brand-purple-soft text-brand-purple-dark'
                      : 'border-neutral-300 text-neutral-700 hover:bg-neutral-50',
                  )}
                >
                  {label}
                </button>
              )
            })}
          </div>
          <Textarea
            placeholder="Anything else? (optional)"
            value={text}
            maxLength={1000}
            onChange={(e) => setText(e.target.value)}
          />
          {error && <span className="text-xs text-red-600">{error}</span>}
          <div>
            <Button onClick={submit} disabled={submitting}>
              {submitting ? 'Saving…' : 'Submit feedback'}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

export function CsatDone({ csat }: { csat: Csat }) {
  return (
    <div className="flex flex-wrap items-center gap-2.5 text-sm text-neutral-700">
      <span className="flex h-[22px] w-[22px] items-center justify-center rounded-full bg-brand-green-soft text-brand-green-dark">
        <Check className="h-3.5 w-3.5" strokeWidth={2.5} />
      </span>
      Thanks for the feedback!
      <Stars value={csat.rating} size="sm" />
      {csat.tags.length > 0 && (
        <span className="text-xs text-neutral-400">· {csat.tags.map((t) => TAG_LABEL[t] ?? t).join(', ')}</span>
      )}
    </div>
  )
}
