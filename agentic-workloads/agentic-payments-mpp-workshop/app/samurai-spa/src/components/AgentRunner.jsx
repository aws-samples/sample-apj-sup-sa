import { useEffect, useRef, useState } from 'react'
import { invokeAgent, getSessionId, newSessionId } from '../agentcore-client.js'
import JsonHighlighter from './JsonHighlighter.jsx'
import ListingDialog from './ListingDialog.jsx'

const SAMPLES = [
  'Bamboo cutting board set, teak handles, for Amazon, priced at $45',
  'Hand-poured vanilla & sandalwood soy candle, 8oz, handmade in SG — Etsy listing',
  'Wireless noise-cancelling earbuds for Shopify, active sellers want lifestyle copy',
  'Non-slip eco yoga mat for Lazada SEA, $29, targeting home fitness',
]

export default function AgentRunner({ onInFlightChange }) {
  const [sessionId, setSessionId] = useState(getSessionId())
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const scrollerRef = useRef(null)

  useEffect(() => onInFlightChange?.(busy), [busy, onInFlightChange])
  useEffect(() => {
    if (scrollerRef.current) scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight
  }, [messages, busy])

  async function send(text) {
    const prompt = (text ?? input).trim()
    if (!prompt || busy) return
    setInput('')
    setError(null)
    setMessages((ms) => [...ms, { role: 'user', content: prompt }])
    setBusy(true)
    try {
      const resp = await invokeAgent(prompt, sessionId)
      setMessages((ms) => [...ms, { role: 'agent', content: resp }])
    } catch (err) {
      setError(err?.message ?? 'unknown error')
    } finally {
      setBusy(false)
    }
  }

  function resetSession() {
    const id = newSessionId()
    setSessionId(id)
    setMessages([])
    setError(null)
  }

  return (
    <div className="h-full flex flex-col bg-[#f7f7f8]">
      <div className="flex items-center gap-2 px-6 py-2 border-b border-black/5 text-xs text-[#7a7a7a]">
        <span>session:</span>
        <code className="font-mono text-[11px] bg-white px-2 py-0.5 rounded border border-black/10">
          {sessionId}
        </code>
        <button onClick={resetSession} className="ml-auto text-xs text-[#635bff] hover:underline">
          reset session
        </button>
      </div>

      <div ref={scrollerRef} className="flex-1 overflow-auto px-6 py-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-sm text-[#7a7a7a] space-y-2">
            <div>Describe a product. Samurai will gather what it needs, then call ListingBot over MPP.</div>
            <div className="pt-2">Or try one:</div>
            <div className="flex flex-col gap-1">
              {SAMPLES.map((s, i) => (
                <button
                  key={i}
                  onClick={() => send(s)}
                  className="text-left text-sm bg-white border border-black/10 rounded-md px-3 py-2 hover:border-[#635bff]"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <Message key={i} message={m} />
        ))}

        {busy && <div className="text-xs text-[#7a7a7a] italic">Samurai is thinking…</div>}

        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-3">
            Error: {error}
          </div>
        )}
      </div>

      <div className="border-t border-black/10 bg-white p-3">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && send()}
            placeholder="Describe a product…"
            disabled={busy}
            className="flex-1 border border-black/10 rounded-md px-3 py-2 outline-none focus:border-[#635bff]"
          />
          <button
            onClick={() => send()}
            disabled={busy || !input.trim()}
            className="px-4 py-2 rounded-md bg-[#635bff] text-white disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}

function Message({ message }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] bg-[#635bff] text-white rounded-lg px-3 py-2 text-sm">
          {message.content}
        </div>
      </div>
    )
  }
  const content = message.content
  const body = content?.response ?? content

  // Listing bubbles are wider so the JSON pre and the dialog button have
  // enough horizontal room. Detect by walking into the Strands envelope.
  const hasListing = messageContainsListing(body)
  const widthClass = hasListing ? 'max-w-[95%] w-full' : 'max-w-[90%]'

  return (
    <div className="flex justify-start">
      <div className={`${widthClass} bg-white border border-black/10 rounded-lg px-3 py-2 text-sm space-y-2`}>
        <div className="text-[11px] uppercase tracking-wide text-[#7a7a7a]">samurai</div>
        {typeof body === 'string' ? (
          renderTextOrListing(body)
        ) : body?.lastMessage?.content ? (
          <StrandsMessageContent blocks={body.lastMessage.content} />
        ) : (
          <JsonHighlighter data={body} />
        )}
      </div>
    </div>
  )
}

function StrandsMessageContent({ blocks }) {
  if (!Array.isArray(blocks)) return <JsonHighlighter data={blocks} />
  return (
    <div className="space-y-2">
      {blocks.map((b, i) => {
        // Wire format from /invocations omits the `type` discriminator —
        // a text block is just `{ text: "..." }`. Duck-type instead.
        if (typeof b?.text === 'string') return <div key={i}>{renderTextOrListing(b.text)}</div>
        return <JsonHighlighter key={i} data={b} />
      })}
    </div>
  )
}

// ── Listing detection + rendering ──────────────────────────────────────────

/** Render a string as either a pretty listing block or plain text. */
function renderTextOrListing(text) {
  const listing = tryParseListing(text)
  if (listing) return <ListingJson listing={listing} />
  return <span className="whitespace-pre-wrap">{text}</span>
}

/** True if any text block within the agent response parses to a listing. */
function messageContainsListing(body) {
  if (typeof body === 'string') return tryParseListing(body) != null
  const blocks = body?.lastMessage?.content
  if (!Array.isArray(blocks)) return false
  return blocks.some(
    (b) => typeof b?.text === 'string' && tryParseListing(b.text) != null,
  )
}

/** Parse a listing JSON out of free text (may be wrapped in markdown fences). */
function tryParseListing(text) {
  if (typeof text !== 'string') return null
  const cleaned = text.trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```\s*$/i, '')
  try {
    const parsed = JSON.parse(cleaned)
    return looksLikeListing(parsed) ? parsed : null
  } catch {
    return null
  }
}

/** Heuristic: any of these structural fields means it's a listing payload. */
function looksLikeListing(obj) {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return false
  return (
    typeof obj.title === 'string' ||
    Array.isArray(obj.bullets) ||
    Array.isArray(obj.bullet_points) ||
    Array.isArray(obj.key_features) ||
    typeof obj.description === 'string'
  )
}

/** Pretty-printed listing JSON with a "View as listing" button in the
 *  top-right corner that opens the ListingDialog modal. */
function ListingJson({ listing }) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const pretty = JSON.stringify(listing, null, 2)
  return (
    <div className="relative">
      <button
        onClick={() => setDialogOpen(true)}
        className="absolute top-1 right-1 text-[11px] bg-[#635bff] text-white px-2 py-1 rounded hover:bg-[#5048d6] z-10 font-medium"
      >
        View as listing
      </button>
      <pre className="text-xs bg-gray-900 text-gray-100 p-3 pr-28 rounded overflow-x-auto whitespace-pre font-mono leading-relaxed">
        {pretty}
      </pre>
      <ListingDialog open={dialogOpen} onOpenChange={setDialogOpen} listing={listing} />
    </div>
  )
}
