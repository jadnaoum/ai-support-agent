import { useEffect } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, friendlyError } from '@/lib/api'
import { replyStats } from '@/lib/stats'
import { useConversation } from '@/hooks/useConversation'
import { Button } from '@/components/ui/button'
import { TopBar } from '@/components/chat/TopBar'
import { MessageList } from '@/components/chat/MessageList'
import { Composer } from '@/components/chat/Composer'
import { CsatFlow, CsatDone } from '@/components/chat/CsatFlow'
import { SummaryCard } from '@/components/chat/SummaryCard'
import { RulesPanel } from '@/components/behind/RulesPanel'
import { Timeline } from '@/components/behind/Timeline'

export default function ChatPage({ readOnly = false }: { readOnly?: boolean }) {
  const { conversationId = '' } = useParams()
  const navigate = useNavigate()
  const conv = useConversation(conversationId)

  useEffect(() => {
    document.title = conv.customer ? `${conv.customer.name} · Break my support agent` : 'Break my support agent'
  }, [conv.customer])

  async function newChatSameCustomer() {
    if (!conv.customer) return
    try {
      const c = await api.createConversation(conv.customer.customer_id)
      navigate(`/chat/${c.conversation_id}`)
    } catch (e) {
      alert(friendlyError(e))
    }
  }

  if (conv.phase === 'loading') {
    return <div className="flex h-screen items-center justify-center text-sm text-neutral-400">Loading…</div>
  }
  if (conv.phase === 'missing' || !conv.customer) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4 text-sm text-neutral-500">
        <p>{conv.error ?? 'This conversation is no longer available.'}</p>
        <Button asChild variant="outline">
          <Link to="/">Pick a scenario</Link>
        </Button>
      </div>
    )
  }

  const customer = conv.customer
  const stats = replyStats(conv.turns.map((t) => t.record))
  const ended = conv.phase === 'ended' || conv.phase === 'rated'

  return (
    <div className="flex min-h-screen flex-col bg-white lg:h-screen">
      <TopBar
        customer={customer}
        stats={stats}
        escalated={conv.escalated}
        ended={ended}
        readOnly={readOnly}
        busy={conv.busy}
        onEnd={conv.endChat}
      />

      <div className="grid flex-1 grid-cols-1 lg:min-h-0 lg:grid-cols-[7fr_5fr]">
        <section className="flex flex-col border-b border-neutral-200 lg:min-h-0 lg:border-b-0 lg:border-r">
          <h2 className="shrink-0 px-6 py-3.5 text-xs font-semibold uppercase tracking-wider text-neutral-400">Chat</h2>
          <MessageList
            messages={conv.messages}
            turns={conv.turns}
            escalated={conv.escalated}
            ended={ended}
          />
          {!readOnly && conv.phase === 'chatting' && (
            <Composer
              busy={conv.busy}
              error={conv.error}
              onSend={conv.send}
              onDismissError={conv.clearError}
            />
          )}
          {!readOnly && conv.phase === 'ended' && (
            <div className="shrink-0 border-t border-neutral-200 px-6 py-5">
              <CsatFlow onSubmit={conv.submitCsat} />
            </div>
          )}
          {(conv.phase === 'rated' || (readOnly && ended)) && (
            <div className="flex shrink-0 flex-col gap-5 border-t border-neutral-200 px-6 py-5 lg:max-h-[70%] lg:overflow-y-auto">
              {conv.csat && <CsatDone csat={conv.csat} />}
              <SummaryCard
                totals={conv.totals}
                stats={stats}
                checks={conv.checks}
                customerName={customer.name}
                conversationId={conversationId}
                readOnly={readOnly}
                onNewChat={newChatSameCustomer}
                onNewScenario={() => navigate('/')}
              />
            </div>
          )}
          {readOnly && !ended && (
            <div className="shrink-0 border-t border-neutral-200 px-6 py-4 text-sm text-neutral-500">
              This conversation is still open. <Link className="text-brand-purple-dark underline" to="/">Start your own</Link>.
            </div>
          )}
        </section>

        <aside className="flex flex-col bg-neutral-50 lg:min-h-0">
          <h2 className="shrink-0 px-6 py-3.5 text-xs font-semibold uppercase tracking-wider text-neutral-400">
            Behind the scenes
          </h2>
          <div className="flex flex-1 flex-col gap-4 px-6 pb-6 lg:min-h-0 lg:overflow-y-auto">
            <RulesPanel customerId={customer.customer_id} />
            <Timeline turns={conv.turns} />
          </div>
        </aside>
      </div>
    </div>
  )
}
