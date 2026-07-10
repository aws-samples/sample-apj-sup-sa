/**
 * Tool: discover_service
 *
 * Fetches the ListingBot service's /openapi.json (free, no payment). Caches
 * the full document in memory for the container's lifetime so repeated calls
 * are instant.
 *
 * The returned document includes:
 *   - paths: the three endpoints and their semantics
 *   - x-payment-info: { unit_price, network, currency, token_contract }
 *   - x-platform-schemas: per-platform JSON Schema listing required +
 *     optional fields. Samurai uses this to figure out what to ask the human.
 */
import { tool } from '@strands-agents/sdk'
import { z } from 'zod'
import { logMppEvent } from '../mpp-logger.js'

let cachedDoc: any = null

async function fetchOpenApi(): Promise<any> {
  if (cachedDoc) return cachedDoc
  const apiUrl = process.env.LISTING_BOT_API_URL
  if (!apiUrl) throw new Error('LISTING_BOT_API_URL not set')
  const url = `${apiUrl.replace(/\/$/, '')}/openapi.json`
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`discover_service: HTTP ${resp.status}`)
  cachedDoc = await resp.json()
  return cachedDoc
}

export const discoverServiceTool = tool({
  name: 'discover_service',
  description:
    'Fetch ListingBot\'s OpenAPI document to learn the input schema per ' +
    'marketplace platform, the price, and the payment network. FREE — no ' +
    'payment is required. Call this once before asking the human for details ' +
    'so you know what to collect.',
  inputSchema: z.object({
    platform: z
      .string()
      .optional()
      .describe('Optional — narrow the returned schema to just this platform'),
  }),
  callback: async ({ platform }) => {
    const doc = await fetchOpenApi()
    await logMppEvent({
      type: 'request',
      label: 'GET /openapi.json (free discovery)',
      detail: { cached: doc === cachedDoc, platform: platform ?? '(all)' },
    })
    const schemas = doc?.['x-platform-schemas'] ?? {}
    const payment = doc?.['x-payment-info'] ?? {}
    const result = platform && schemas[platform]
      ? { platform, schema: schemas[platform], payment, available_platforms: Object.keys(schemas) }
      : { schemas, payment, available_platforms: Object.keys(schemas) }
    return JSON.parse(JSON.stringify(result))
  },
})
