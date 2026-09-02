// Shared contract with the backend (backend/routers/demo.py, backend/demo/events.py).

export type OrderStatus = 'processing' | 'shipped' | 'delivered' | 'cancelled' | 'returned'

export interface DemoOrder {
  order_id: string
  product_name: string
  category: string
  status: OrderStatus
  order_date: string | null
  ordered_label: string
}

export interface DemoCustomer {
  customer_id: string
  name: string
  email: string
  scenario_id: string
  scenario_title: string
  demo: boolean
  orders: DemoOrder[]
  suggested_prompts: string[]
}

export interface CatalogOrder {
  product_id: string
  product_name: string
  category: string
  status: OrderStatus
  ordered_relative: string
  ordered_label: string
}

export interface Scenario {
  id: string
  title: string
  dare: string
  orders: CatalogOrder[]
  suggested_prompts: string[]
}

export interface Catalog {
  names: string[]
  scenarios: Scenario[]
  statuses: OrderStatus[]
  relative_dates: { id: string; label: string }[]
  max_orders: number
  common_prompts: string[]
}

export interface Product {
  id: string
  name: string
  category: string
}

export interface OrderSpec {
  product_id: string
  status: OrderStatus | ''
  ordered_relative: string
}

export interface Rule {
  text: string
  source: string
  product_id?: string
}

export interface Rules {
  account: Rule[]
  global: Rule[]
}

// ---- pipeline events ------------------------------------------------------

export interface InputGuardEvent {
  type: 'input_guard'
  verdict: 'safe' | 'blocked'
  reason: string | null
  strike: number | null
}
export interface ToolCallEvent {
  type: 'tool_call'
  tool: string
  result: string
  success: boolean
  order_id: string | null
  confirmation_required: boolean
  requires_escalation: boolean
  duration_ms: number | null
}
export interface KbRetrievalEvent {
  type: 'kb_retrieval'
  query: string
  articles: string[] | null
  chunks: number
  top_similarity: number | null
  success: boolean
}
export interface OutputGuardEvent {
  type: 'output_guard'
  verdict: 'pass' | 'flagged'
  reason: string | null
}
export interface EscalationEvent {
  type: 'escalation'
  reason: string
  context_summary: string
}
export interface ErrorEvent {
  type: 'error'
  kind: string
  message: string
}
export type PipelineEvent =
  | InputGuardEvent
  | ToolCallEvent
  | KbRetrievalEvent
  | OutputGuardEvent
  | EscalationEvent
  | ErrorEvent

export type CostBasis = 'measured' | 'partial' | 'unavailable'

export interface TurnRecord {
  index: number
  events: PipelineEvent[]
  latency_ms: number | null
  llm_calls: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  cost_usd: number | null
  cost_basis: CostBasis
  requires_escalation: boolean
  escalation_reason: string | null
  context_summary: string | null
  error: string | null
}

export interface TimelineTurn extends TurnRecord {
  started_at: string | null
  customer_message: string | null
  response: string | null
}

export interface Check {
  id: string
  status: 'pass' | 'fail' | 'na'
  text: string
}

export interface Totals {
  messages: number
  turns: number
  latency_ms: number
  llm_calls: number
  cost_usd: number | null
  cost_basis: CostBasis
}

export interface Csat {
  rating: number
  tags: string[]
  free_text: string | null
}

export interface Timeline {
  conversation: {
    id: string
    status: 'active' | 'resolved' | 'escalated'
    started_at: string | null
    ended_at: string | null
    csat: Csat | null
  }
  customer: DemoCustomer | null
  messages: { id: string; role: 'customer' | 'agent'; content: string; created_at: string | null }[]
  turns: TimelineTurn[]
  totals: Totals
  checks: Check[]
}
