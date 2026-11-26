import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'
import { api, friendlyError } from '@/lib/api'
import type { Catalog, OrderSpec, Product, Scenario } from '@/lib/types'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { SectionLabel } from '@/components/SectionLabel'
import { ScenarioCard } from '@/components/setup/ScenarioCard'
import { OrderBuilder } from '@/components/setup/OrderBuilder'

interface Pick {
  scenario: Scenario
  name: string
}

function shuffle<T>(xs: T[]): T[] {
  const a = [...xs]
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

const EMPTY_ROW: OrderSpec = { product_id: '', status: '', ordered_relative: '' }

export default function SetupPage() {
  const navigate = useNavigate()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [names, setNames] = useState<string[]>([])
  const [scenarioOrder, setScenarioOrder] = useState<Scenario[]>([])
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [rows, setRows] = useState<OrderSpec[]>([EMPTY_ROW])
  const [starting, setStarting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.catalog(), api.products()])
      .then(([c, p]) => {
        setCatalog(c)
        setProducts(p)
        setNames(c.names)
        setScenarioOrder(shuffle(c.scenarios))
      })
      .catch((e) => setError(friendlyError(e)))
  }, [])

  const picks: Pick[] = useMemo(
    () => scenarioOrder.slice(0, 3).map((scenario, i) => ({ scenario, name: names[i] ?? '' })),
    [scenarioOrder, names],
  )
  const builderNames = names.slice(3, 5)

  function reshuffle() {
    setScenarioOrder((s) => shuffle(s))
    setNames((n) => shuffle(n))
    setSelectedName(null)
  }

  async function start(name: string, body: { scenario_id?: string; orders?: OrderSpec[] }, key: string) {
    setStarting(key)
    setError(null)
    try {
      const customer = await api.createCustomer({ name, ...body })
      const conv = await api.createConversation(customer.customer_id)
      navigate(`/chat/${conv.conversation_id}`)
    } catch (e) {
      setError(friendlyError(e))
      setStarting(null)
    }
  }

  const completeRows = rows.filter((r) => r.product_id && r.status && r.ordered_relative)
  const canStartCustom =
    !!selectedName && completeRows.length > 0 && completeRows.length === rows.length && !starting

  return (
    <div className="min-h-screen bg-neutral-50">
      <div className="mx-auto flex w-full max-w-[1040px] flex-col gap-10 px-6 py-14">
        <header className="flex flex-col gap-2.5">
          <h1 className="text-4xl font-bold tracking-tight text-neutral-900">Break my support agent</h1>
          <p className="max-w-2xl text-base leading-relaxed text-neutral-500">
            or just try it out
          </p>
        </header>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
        )}

        <section className="flex flex-col gap-4">
          <div className="flex items-baseline justify-between">
            <SectionLabel tone="green" size="lg">
              Pick a scenario
            </SectionLabel>
            <button
              type="button"
              onClick={reshuffle}
              disabled={!catalog}
              className="flex items-center gap-1.5 text-[13px] text-brand-purple-dark hover:underline disabled:opacity-50"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Shuffle scenarios
            </button>
          </div>
          {!catalog ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <Card key={i} className="h-64 animate-soft-pulse bg-neutral-100" />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              {picks.map((p) => (
                <ScenarioCard
                  key={p.scenario.id}
                  name={p.name}
                  scenario={p.scenario}
                  starting={starting === p.scenario.id}
                  disabled={!!starting}
                  onStart={() => start(p.name, { scenario_id: p.scenario.id }, p.scenario.id)}
                />
              ))}
            </div>
          )}
        </section>

        <section className="flex flex-col gap-4">
          <SectionLabel tone="purple" size="lg">
            Or build your own
          </SectionLabel>
          <Card className="flex flex-col gap-5 p-6">
            <div className="flex flex-col gap-2">
              <span className="text-[13px] font-medium text-neutral-700">Who are you?</span>
              <div className="flex gap-2">
                {builderNames.map((n) => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => setSelectedName(n)}
                    className={
                      'rounded-lg border px-4 py-2 text-sm transition-colors ' +
                      (selectedName === n
                        ? 'border-brand-purple bg-brand-purple-soft text-brand-purple-dark'
                        : 'border-neutral-300 bg-white text-neutral-800 hover:bg-neutral-50')
                    }
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-2">
              <span className="text-[13px] font-medium text-neutral-700">Your orders</span>
              <OrderBuilder
                products={products}
                statuses={catalog?.statuses ?? []}
                relativeDates={catalog?.relative_dates ?? []}
                maxOrders={catalog?.max_orders ?? 3}
                rows={rows}
                onChange={setRows}
              />
            </div>

            <div className="flex items-center gap-3 pt-1">
              <Button
                className="bg-brand-purple text-white hover:bg-brand-purple/90"
                disabled={!canStartCustom}
                onClick={() =>
                  selectedName && start(selectedName, { orders: completeRows }, 'custom')
                }
              >
                {starting === 'custom' ? 'Setting up…' : 'Start chatting'}
              </Button>
              {!canStartCustom && (
                <span className="text-xs text-neutral-400">Pick a name and complete at least one order</span>
              )}
            </div>
          </Card>
        </section>
      </div>
    </div>
  )
}
