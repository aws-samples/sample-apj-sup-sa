import { useEffect, useRef, useState } from 'react'
import JsonHighlighter from './JsonHighlighter.jsx'
import { fetchMppLogs } from '../mpp-logs-client.js'
import { getSessionId } from '../agentcore-client.js'

/**
 * MPP debug panel. Adaptive polling (100ms in-flight, 1s idle) against the
 * MPP logs DynamoDB table. Reads happen directly with Cognito-vended creds —
 * no Lambda hop.
 */
export default function DebugPanel({ inFlight }) {
  const [events, setEvents] = useState([])
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(new Set())
  const sinceRef = useRef(0)
  const timerRef = useRef(null)
  const sessionId = getSessionId()

  useEffect(() => {
    let cancelled = false

    async function tick() {
      try {
        const data = await fetchMppLogs(sessionId, sinceRef.current)
        if (cancelled) return
        if (Array.isArray(data?.items) && data.items.length > 0) {
          sinceRef.current = data.lastSeq ?? sinceRef.current
          setEvents((prev) => [...prev, ...data.items])
          setExpanded((prev) => {
            const next = new Set(prev)
            for (const it of data.items) if (it.type === '200') next.add(it.seq)
            return next
          })
        }
        setError(null)
      } catch (e) {
        if (!cancelled) setError(e?.message ?? 'poll error')
      }
      if (cancelled) return
      const delay = inFlight ? 100 : 1000
      timerRef.current = setTimeout(tick, delay)
    }

    tick()
    return () => {
      cancelled = true
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [inFlight, sessionId])

  function toggle(seq) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(seq)) next.delete(seq)
      else next.add(seq)
      return next
    })
  }

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="px-4 py-3 border-b border-black/5 flex items-center justify-between">
        <div>
          <div className="font-semibold text-sm">MPP debug panel</div>
          <div className="text-[11px] text-[#7a7a7a]">
            Request → 402 Challenge → Mint SPT → Retry → 200 Paid
          </div>
          <div className="text-[10px] text-[#9a9a9a]">
            polling {inFlight ? '100 ms (in-flight)' : '1 s (idle)'}
          </div>
        </div>
        {error && <div className="text-[11px] text-red-500">⚠ {error}</div>}
      </div>

      <div className="flex-1 overflow-auto px-3 py-2 space-y-1">
        {events.length === 0 ? (
          <div className="text-xs text-[#7a7a7a] italic p-3">
            No MPP activity yet. Send a message — Samurai's generate_listing tool
            will trigger the 402 → pay → retry dance, and every step will land
            here.
          </div>
        ) : (
          events
            .slice()
            .reverse()
            .map((ev) => (
              <EventRow key={ev.seq} ev={ev} expanded={expanded.has(ev.seq)} onToggle={() => toggle(ev.seq)} />
            ))
        )}
      </div>
    </div>
  )
}

const BADGE = {
  request: { label: 'REQ', color: 'bg-blue-500' },
  '402': { label: '402', color: 'bg-orange-500' },
  spt: { label: 'SPT', color: 'bg-indigo-500' },
  retry: { label: 'RETRY', color: 'bg-cyan-500' },
  '200': { label: '200', color: 'bg-green-500' },
  error: { label: 'ERR', color: 'bg-red-500' },
}

const SUBTITLE = {
  request: 'Agent sends initial request',
  '402': 'Server demands payment — here are the terms',
  spt: 'Agent mints a one-time SPT scoped to the seller',
  retry: 'Agent attached its SPT credential and resent',
  '200': 'SPT accepted, listing generated',
}

function EventRow({ ev, expanded, onToggle }) {
  const b = BADGE[ev.type] ?? { label: ev.type, color: 'bg-gray-500' }
  const subtitle = SUBTITLE[ev.type]
  return (
    <div className="border border-black/10 rounded-md overflow-hidden">
      <button onClick={onToggle} className="w-full flex items-start gap-2 px-2 py-1.5 text-left hover:bg-black/[0.02]">
        <span className={`text-[10px] font-mono text-white px-1.5 py-0.5 rounded mt-0.5 ${b.color}`}>{b.label}</span>
        <span className="flex-1 min-w-0">
          <span className="text-[13px] block truncate">{ev.label}</span>
          {subtitle && <span className="text-[10px] text-[#7a7a7a] block truncate">{subtitle}</span>}
        </span>
        <span className="text-[10px] text-[#7a7a7a] mt-0.5">#{ev.seq}</span>
        <span className="text-[10px] text-[#7a7a7a] mt-0.5">{expanded ? '▾' : '▸'}</span>
      </button>
      {expanded && (
        <div className="px-2 pb-2 border-t border-black/5 bg-black/[0.01]">
          <JsonHighlighter data={ev.detail} />
          <div className="text-[10px] text-[#7a7a7a] mt-1">{ev.ts}</div>
        </div>
      )}
    </div>
  )
}
