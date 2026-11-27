import type { PipelineEvent } from './types'
import { basedOn } from './basedOn'
import { fmtDuration, humanize } from './format'

/** What the step is. Always drawn grey; colour lives only in the status slot. */
export type StepKind = 'safety' | 'tool' | 'kb' | 'decide' | 'write' | 'verify' | 'send' | 'wait' | 'error'

/**
 * The only states a step can be in:
 *   pending = not reached yet, running = happening now, done = finished,
 *   stopped = a rule stepped in (blocked, needs confirmation, not eligible, handed off),
 *   error   = infrastructure failure
 */
export type StepStatus = 'pending' | 'running' | 'done' | 'stopped' | 'error'

export interface StepView {
  kind: StepKind
  status: StepStatus
  title: string
  /** Tool name, rendered as small monospace text at the start of the detail line. */
  code?: string
  detail?: string
  right?: string
}

const TOOL_TITLE: Record<string, string> = {
  track_order: 'Where is the order?',
  check_cancel_eligibility: 'Can this order be cancelled?',
  check_return_eligibility: 'Can this order be returned?',
  cancel_order: 'Cancel the order',
  initiate_return: 'Start the return',
  check_missing_package: 'Check the missing package',
  get_refund_status: 'Look up refunds',
}

const TOOL_RESULT_LABEL: Record<string, string> = {
  success: 'done',
  confirmation_pending: 'waiting for your confirmation first',
  eligible: 'yes',
  outside_return_window: 'no, outside the return window',
  final_sale: 'no, final sale',
  non_returnable_category: 'no, not a returnable category',
  already_shipped: 'no, already shipped',
  already_delivered: 'no, already delivered',
  already_cancelled: 'already cancelled',
  already_returned: 'already returned',
  already_refunded: 'already refunded',
  already_in_progress: 'return already in progress',
  wrong_status: 'not possible in this order status',
  defective: 'defective claim, needs a human',
  duplicate_item: 'duplicate item, needs a human',
  carrier_claim_eligible: 'carrier claim, needs a human',
  wait_for_delivery: 'still in transit',
  order_id_required: 'asked which order',
  reason_required: 'asked for a reason',
  invalid_reason: 'reason not recognised',
  tracking_info: 'tracking found',
  refund_list: 'refunds found',
  no_refunds: 'no refunds on file',
  cancelled: 'cancelled',
  return_initiated: 'return started',
  return_pending_review: 'sent for human review',
  failed: 'failed',
}

function verifyDetail(events: PipelineEvent[]): string {
  const b = basedOn(events)
  if (!b) return 'Passed'
  const parts: string[] = []
  if (b.sources.some((s) => s === 'your order data')) parts.push('your order data')
  if (b.sources.some((s) => s.endsWith('(KB)'))) parts.push('the policies it looked up')
  return `Matches ${parts.join(' and ')}`
}

/** One recorded event as a step. `all` is the whole turn, for the verification detail. */
export function stepView(e: PipelineEvent, all: PipelineEvent[]): StepView {
  switch (e.type) {
    case 'input_guard':
      if (e.verdict === 'safe') return { kind: 'safety', status: 'done', title: 'Safety check', detail: 'Message is fine' }
      return {
        kind: 'safety',
        status: 'stopped',
        title: 'Safety check',
        detail: `Blocked · ${humanize(e.reason)}${e.strike ? ` (strike ${e.strike} of 3)` : ''}`,
      }
    case 'tool_call': {
      const base = { kind: 'tool' as const, title: TOOL_TITLE[e.tool] ?? humanize(e.tool), code: e.tool, right: fmtDuration(e.duration_ms) }
      const label = TOOL_RESULT_LABEL[e.result] ?? humanize(e.result)
      if (e.result === 'failed') return { ...base, status: 'error', detail: 'failed' }
      if (e.success && !e.requires_escalation && !e.confirmation_required) return { ...base, status: 'done', detail: label }
      return { ...base, status: 'stopped', detail: label }
    }
    case 'kb_retrieval': {
      const base = { kind: 'kb' as const, title: 'Looked up the policies' }
      if (!e.success) return { ...base, status: 'error', detail: 'Lookup failed' }
      if (e.articles && e.articles.length) return { ...base, status: 'done', detail: e.articles.join(' · ') }
      if (e.articles === null && e.chunks > 0) return { ...base, status: 'done', detail: `${e.chunks} passages` }
      return { ...base, status: 'done', detail: 'Nothing relevant found' }
    }
    case 'output_guard':
      if (e.verdict === 'pass') return { kind: 'verify', status: 'done', title: 'Checked the reply', detail: verifyDetail(all) }
      return {
        kind: 'verify',
        status: 'stopped',
        title: 'Checked the reply',
        detail: `Flagged · ${humanize(e.reason)} · reply withheld`,
      }
    case 'escalation':
      return { kind: 'send', status: 'stopped', title: 'Handed to a human', detail: humanize(e.reason) }
    case 'error':
      return { kind: 'error', status: 'error', title: e.kind === 'graph_timeout' ? 'Timed out' : 'System error', detail: e.message }
  }
}

const blocked = (events: PipelineEvent[]) => events.some((e) => e.type === 'input_guard' && e.verdict === 'blocked')

/**
 * The rows for a turn. Writing the reply has no event of its own, so a
 * "Wrote the reply" row is placed before the reply check (or at the end).
 * While live, the step most likely running is appended with a spinner,
 * followed by the steps every reply still has to go through.
 */
export function turnSteps(events: PipelineEvent[], live: boolean): StepView[] {
  const shown = events.filter((e) => e.type !== 'escalation')
  const steps = shown.map((e) => stepView(e, events))
  const failed = events.some((e) => e.type === 'error')
  const verifyAt = shown.findIndex((e) => e.type === 'output_guard')
  const wrote: StepView = { kind: 'write', status: 'done', title: blocked(events) ? 'Wrote a redirect' : 'Wrote the reply' }

  if (!live) {
    if (!failed) steps.splice(verifyAt === -1 ? steps.length : verifyAt, 0, wrote)
    return steps
  }

  const last = events[events.length - 1]
  const pendingWrite: StepView = { kind: 'write', status: 'pending', title: 'Write the reply' }
  const pendingCheck: StepView = { kind: 'verify', status: 'pending', title: 'Check the reply' }
  const run = (kind: StepKind, title: string): StepView => ({ kind, status: 'running', title })

  if (!last) return [run('safety', 'Safety check'), pendingWrite, pendingCheck]
  if (verifyAt !== -1) {
    steps.splice(verifyAt, 0, wrote)
    return [...steps, run('send', 'Sending')]
  }
  if (blocked(events)) return [...steps, run('write', 'Writing a redirect')]
  if (last.type === 'kb_retrieval') return [...steps, run('write', 'Writing the reply'), pendingCheck]
  if (last.type === 'input_guard') return [...steps, run('decide', 'Deciding what to do'), pendingWrite, pendingCheck]
  return [...steps, run('decide', 'Deciding what to do with the result'), pendingWrite, pendingCheck]
}

/** Short label for the chat placeholder: what the pipeline is most likely doing now. */
export function nextStepLabel(events: PipelineEvent[]): string {
  return turnSteps(events, true).find((s) => s.status === 'running')?.title ?? 'Working'
}
