import type { PipelineEvent } from './types'
import type { Tone } from './tones'
import { fmtDuration, humanize } from './format'

export type StepIcon = 'check' | 'shield' | 'tool' | 'book' | 'alert' | 'error' | 'handoff' | 'minus' | 'spinner'

export interface StepView {
  tone: Tone
  icon: StepIcon
  /** Main text; `code` is rendered in monospace before `label`. */
  code?: string
  label: string
  /** Softer trailing text. */
  detail?: string
  right?: string
}

const TOOL_RESULT_LABEL: Record<string, string> = {
  success: 'done',
  confirmation_pending: 'asked you to confirm first',
  eligible: 'eligible',
  outside_return_window: 'outside the return window',
  final_sale: 'final sale, not returnable',
  non_returnable_category: 'not a returnable category',
  already_shipped: 'already shipped',
  already_delivered: 'already delivered',
  already_cancelled: 'already cancelled',
  already_returned: 'already returned',
  already_refunded: 'already refunded',
  already_in_progress: 'return already in progress',
  wrong_status: 'wrong order status',
  defective: 'defective claim, needs a human',
  duplicate_item: 'duplicate item, needs a human',
  carrier_claim_eligible: 'carrier claim, needs a human',
  wait_for_delivery: 'still in transit',
  order_id_required: 'which order?',
  reason_required: 'needs a reason',
  invalid_reason: 'reason not recognised',
  tracking_info: 'tracking found',
  refund_list: 'refunds found',
  no_refunds: 'no refunds on file',
  cancelled: 'cancelled',
  return_initiated: 'return started',
  return_pending_review: 'return sent for human review',
  failed: 'failed',
}

export function stepView(e: PipelineEvent): StepView {
  switch (e.type) {
    case 'input_guard':
      if (e.verdict === 'safe') return { tone: 'green', icon: 'check', label: 'Safety check', detail: 'passed' }
      return {
        tone: 'purple',
        icon: 'shield',
        label: 'Safety check blocked',
        detail: `${humanize(e.reason)}${e.strike ? ` · strike ${e.strike} of 3` : ''}`,
      }
    case 'tool_call': {
      const label = TOOL_RESULT_LABEL[e.result] ?? humanize(e.result)
      const right = fmtDuration(e.duration_ms)
      if (e.requires_escalation || e.confirmation_required)
        return { tone: 'purple', icon: 'tool', code: e.tool, label: `→ ${label}`, right }
      if (e.success) return { tone: 'green', icon: 'tool', code: e.tool, label: `→ ${label}`, right }
      if (e.result === 'failed') return { tone: 'red', icon: 'error', code: e.tool, label: '→ failed', right }
      return { tone: 'amber', icon: 'tool', code: e.tool, label: `→ ${label}`, right }
    }
    case 'kb_retrieval': {
      if (!e.success) return { tone: 'red', icon: 'error', label: 'KB lookup failed' }
      if (e.articles && e.articles.length)
        return { tone: 'blue', icon: 'book', label: 'KB lookup', detail: e.articles.join(', ') }
      if (e.articles === null && e.chunks > 0)
        return { tone: 'blue', icon: 'book', label: 'KB lookup', detail: `${e.chunks} passages` }
      return { tone: 'amber', icon: 'book', label: 'KB lookup', detail: 'nothing relevant found' }
    }
    case 'output_guard':
      if (e.verdict === 'pass')
        return { tone: 'green', icon: 'check', label: 'Response verification', detail: 'passed' }
      return {
        tone: 'purple',
        icon: 'shield',
        label: 'Response verification flagged',
        detail: `${humanize(e.reason)} · response withheld`,
      }
    case 'escalation':
      return { tone: 'purple', icon: 'handoff', label: 'Handed to a human', detail: humanize(e.reason) }
    case 'error':
      return { tone: 'red', icon: 'error', label: e.kind === 'graph_timeout' ? 'Timed out' : 'System error', detail: e.message }
  }
}
