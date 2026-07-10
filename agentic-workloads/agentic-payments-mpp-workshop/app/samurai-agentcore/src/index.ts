import express from 'express'
import { buildAgent } from './agent.js'
import { setSessionContext } from './mpp-logger.js'
import { loadHistory, saveTurn } from './memory.js'

const PORT = Number(process.env.PORT || 8080)
const app = express()

app.get('/ping', (_req, res) => {
  res.json({ status: 'Healthy', time_of_last_update: Math.floor(Date.now() / 1000) })
})

app.post('/invocations', express.raw({ type: '*/*', limit: '2mb' }), async (req, res) => {
  try {
    const raw = new TextDecoder().decode(req.body as Buffer)
    let parsed: { prompt?: string; sessionId?: string }
    try { parsed = JSON.parse(raw) } catch { parsed = { prompt: raw } }

    const prompt = parsed.prompt ?? ''
    // AgentCore always sends us a session id via header; prefer it, fall
    // back to client-sent value, then a synthetic one.
    const hdrSession =
      req.header('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id') ||
      req.header('x-amzn-bedrock-agentcore-runtime-session-id')
    const sessionId = hdrSession || parsed.sessionId || `sess_${Math.random().toString(36).slice(2, 12)}`
    setSessionContext(sessionId)

    // The container is stateless across requests: every /invocations rebuilds
    // a fresh Agent. Continuity comes from AgentCore Memory — loadHistory()
    // returns prior turns (or [] when MEMORY_ID is unset, e.g. Stage A of the
    // workshop), and we seed them into the new Agent's `messages` so the
    // model sees the conversation so far.
    const history = await loadHistory(sessionId)
    const agent = buildAgent({ messages: history })

    const result = await agent.invoke(prompt)

    // Persist this turn so the next /invocations call can replay it. We save
    // a plain text version of the assistant reply (Memory's conversational
    // payload only carries text). No-op when MEMORY_ID is unset.
    await saveTurn(sessionId, prompt, extractText(result))

    // Return the full Strands AgentResult — the SPA reads
    // `response.lastMessage.content[]` to render text/listing blocks.
    res.json({ sessionId, response: result })
  } catch (err) {
    console.error('invoke error', err)
    res.status(500).json({ error: 'invoke_failed', message: (err as Error)?.message })
  }
})

function extractText(result: unknown): string {
  // AgentResult.lastMessage.content is a ContentBlock[]; pull every text
  // block and concatenate. Tool use / reasoning blocks are skipped.
  const msg = (result as { lastMessage?: { content?: unknown[] } }).lastMessage
  const blocks = msg?.content ?? []
  const parts: string[] = []
  for (const b of blocks) {
    const text = (b as { text?: string }).text
    if (typeof text === 'string') parts.push(text)
  }
  return parts.join('\n').trim()
}

app.listen(PORT, () => {
  console.log(`Samurai AgentCore Runtime listening on :${PORT}`)
})
