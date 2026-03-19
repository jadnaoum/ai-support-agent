import { useState, useEffect, useRef } from 'react'
import { useSSE } from './hooks/useSSE.js'

export default function App() {
  const [customers, setCustomers] = useState([])
  const [selectedCustomer, setSelectedCustomer] = useState(null)
  const [conversationId, setConversationId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState(null)
  const bottomRef = useRef(null)
  const { connect } = useSSE()

  // Load demo customers on mount
  useEffect(() => {
    fetch('/api/customers')
      .then(r => r.json())
      .then(setCustomers)
      .catch(() => setError('Could not load customers — is the backend running?'))
  }, [])

  // Auto-scroll to latest message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function selectCustomer(customer) {
    setSelectedCustomer(customer)
    setMessages([])
    setError(null)
    const res = await fetch('/api/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ customer_id: customer.id }),
    })
    const data = await res.json()
    setConversationId(data.conversation_id)
  }

  async function sendMessage() {
    if (!input.trim() || !conversationId || streaming) return
    const text = input.trim()
    setInput('')
    setError(null)

    // Append customer message immediately
    setMessages(prev => [...prev, { role: 'customer', content: text }])

    // Submit to backend
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: conversationId,
        customer_id: selectedCustomer.id,
        message: text,
      }),
    })
    if (!res.ok) {
      setError('Failed to send message.')
      return
    }

    // Add placeholder for streaming agent response
    setStreaming(true)
    setMessages(prev => [...prev, { role: 'agent', content: '' }])

    connect(conversationId, {
      onToken: (token) => {
        setMessages(prev => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          updated[updated.length - 1] = { ...last, content: last.content + token }
          return updated
        })
      },
      onDone: () => setStreaming(false),
      onError: () => {
        setStreaming(false)
        setError('Stream error — check the backend logs.')
      },
    })
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b px-4 py-3 flex items-center gap-4">
        <h1 className="text-sm font-semibold text-gray-800 shrink-0">AI Support Agent</h1>
        <select
          className="border rounded px-2 py-1 text-sm text-gray-700 bg-white flex-1 max-w-xs"
          value={selectedCustomer?.id || ''}
          onChange={e => {
            const c = customers.find(c => c.id === e.target.value)
            if (c) selectCustomer(c)
          }}
        >
          <option value="">Select a customer…</option>
          {customers.map(c => (
            <option key={c.id} value={c.id}>{c.name} — {c.email}</option>
          ))}
        </select>
        {conversationId && (
          <span className="text-xs text-gray-400 font-mono truncate">
            conv: {conversationId.slice(0, 8)}…
          </span>
        )}
      </header>

      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {messages.length === 0 && (
          <p className="text-center text-gray-400 text-sm mt-8">
            {selectedCustomer
              ? 'Start the conversation by typing a message below.'
              : 'Select a customer from the dropdown to begin.'}
          </p>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'customer' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-lg px-4 py-2 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap ${
              msg.role === 'customer'
                ? 'bg-blue-500 text-white rounded-br-sm'
                : 'bg-white border text-gray-800 rounded-bl-sm shadow-sm'
            }`}>
              {msg.content || (streaming && i === messages.length - 1
                ? <span className="text-gray-400">▋</span>
                : null
              )}
            </div>
          </div>
        ))}
        {error && (
          <p className="text-center text-red-500 text-xs">{error}</p>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="bg-white border-t px-4 py-3 flex gap-2">
        <input
          className="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
          placeholder={selectedCustomer ? 'Type a message…' : 'Select a customer first'}
          value={input}
          disabled={!selectedCustomer || streaming}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && sendMessage()}
        />
        <button
          className="px-4 py-2 bg-blue-500 hover:bg-blue-600 text-white text-sm font-medium rounded-lg disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          onClick={sendMessage}
          disabled={!selectedCustomer || !input.trim() || streaming}
        >
          Send
        </button>
      </div>
    </div>
  )
}
