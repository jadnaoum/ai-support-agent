import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'

export function Composer({
  busy,
  error,
  onSend,
  onDismissError,
}: {
  busy: boolean
  error: string | null
  onSend: (text: string) => void
  onDismissError: () => void
}) {
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!busy) inputRef.current?.focus()
  }, [busy])

  function submit() {
    if (!text.trim() || busy) return
    onSend(text)
    setText('')
  }

  return (
    <div className="flex shrink-0 flex-col gap-2.5 border-t border-neutral-200 px-6 pb-4 pt-3">
      {error && (
        <button
          type="button"
          onClick={onDismissError}
          className="rounded-md bg-amber-50 px-3 py-2 text-left text-xs text-amber-800"
        >
          {error}
        </button>
      )}
      <div className="flex gap-2">
        <input
          ref={inputRef}
          className="h-[42px] flex-1 rounded-lg border border-neutral-300 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-purple disabled:bg-neutral-50 disabled:text-neutral-400"
          placeholder={busy ? 'Waiting for the agent…' : 'Type a message…'}
          value={text}
          disabled={busy}
          maxLength={500}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
        />
        <Button className="h-[42px] px-[18px]" onClick={submit} disabled={busy || !text.trim()}>
          Send
        </Button>
      </div>
    </div>
  )
}
