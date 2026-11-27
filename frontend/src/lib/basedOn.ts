import type { PipelineEvent } from './types'

/** Tool results that mean the tool never actually read order data. */
const NO_DATA_RESULTS = new Set(['failed', 'order_id_required', 'invalid_reason'])

export interface BasedOn {
  /** What the reply drew on, e.g. ["Returns and Refunds (KB)", "your order data"]. */
  sources: string[]
  /** Output guard verdict for the reply; null when verification did not run. */
  verification: 'pass' | 'flagged' | null
}

/**
 * Where a finished reply's information came from, read straight off the
 * turn's recorded pipeline events. It names sources; it does not claim the
 * reply text matches them (that is what the verification verdict is for).
 */
export function basedOn(events: PipelineEvent[]): BasedOn | null {
  const sources: string[] = []
  const articles = new Set<string>()
  let orderData = false
  let verification: BasedOn['verification'] = null

  for (const e of events) {
    if (e.type === 'kb_retrieval' && e.success && e.chunks > 0) {
      for (const a of e.articles ?? []) articles.add(a)
    } else if (e.type === 'tool_call' && !NO_DATA_RESULTS.has(e.result)) {
      orderData = true
    } else if (e.type === 'output_guard') {
      verification = e.verdict
    } else if (e.type === 'input_guard' && e.verdict === 'blocked') {
      return null
    }
  }

  if (articles.size) sources.push(`${[...articles].join(', ')} (KB)`)
  if (orderData) sources.push('your order data')
  if (!sources.length) return null
  return { sources, verification }
}
