/**
 * Tool: check_completeness
 *
 * Calls ListingBot's /validate endpoint (free, deterministic). Use this
 * before calling generate_listing to confirm we have everything we need.
 * The server runs identical rules on the paid path, so a green /validate
 * means /generate will not return 422.
 */
import { tool } from '@strands-agents/sdk'
import { z } from 'zod'
import { logMppEvent } from '../mpp-logger.js'

export const checkCompletenessTool = tool({
  name: 'check_completeness',
  description:
    'Validate a listing payload against ListingBot\'s rules without paying. ' +
    'Returns { valid, errors: [{field, code, message}] }. FREE. Call before ' +
    'generate_listing so the human is never charged for a payload that would ' +
    'fail validation.',
  inputSchema: z.object({
    payload: z.record(z.string(), z.unknown())
      .describe('The payload you would pass to generate_listing'),
  }),
  callback: async ({ payload }) => {
    const apiUrl = process.env.LISTING_BOT_API_URL
    if (!apiUrl) throw new Error('LISTING_BOT_API_URL not set')
    const url = `${apiUrl.replace(/\/$/, '')}/validate`
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const data = await resp.json().catch(() => ({}))
    await logMppEvent({
      type: 'request',
      label: 'POST /validate (free check)',
      detail: { valid: data?.valid, errorCount: (data?.errors || []).length },
    })
    return data
  },
})
