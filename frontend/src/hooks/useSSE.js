import { useRef } from 'react'

/**
 * Returns a function that opens an SSE connection to the stream endpoint.
 * Calls onToken for each token, onDone when the stream ends, onError on failure.
 * Returns a cleanup function that closes the EventSource.
 */
export function useSSE() {
  const esRef = useRef(null)

  function connect(conversationId, { onToken, onDone, onError }) {
    // Close any existing connection
    esRef.current?.close()

    const es = new EventSource(`/api/chat/stream/${conversationId}`)
    esRef.current = es

    es.addEventListener('token', (e) => {
      onToken(e.data)
    })

    es.addEventListener('done', () => {
      es.close()
      esRef.current = null
      onDone()
    })

    es.addEventListener('error', (e) => {
      es.close()
      esRef.current = null
      onError(e)
    })
  }

  function close() {
    esRef.current?.close()
    esRef.current = null
  }

  return { connect, close }
}
