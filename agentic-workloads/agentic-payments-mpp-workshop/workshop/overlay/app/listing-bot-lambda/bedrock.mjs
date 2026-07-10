/**
 * Bedrock Converse — WORKSHOP STARTER VERSION.
 *
 * You will finish TODO 3 in this file — invoke Claude Sonnet 4.6 via
 * Converse with a platform-aware system prompt.
 */
import { BedrockRuntimeClient, ConverseCommand } from '@aws-sdk/client-bedrock-runtime'
import { rules } from './validation.mjs'

const bedrock = new BedrockRuntimeClient({
  region: process.env.BEDROCK_REGION || process.env.AWS_REGION || 'us-east-1',
})

const MODEL_ID = process.env.BEDROCK_MODEL_ID || 'global.anthropic.claude-sonnet-4-6'

function systemPromptFor(platform) {
  const block = rules[platform] ?? rules.Generic
  const guidance = block.guidance ?? ''
  return [
    'You are ListingBot, an expert marketplace copywriter. Write a polished',
    `listing for the ${platform} marketplace.`,
    guidance && `Platform guidance: ${guidance}`,
    'Output MUST be a single JSON object with these keys:',
    '  title         — string, platform-compliant title',
    '  description   — string, well-written long-form description',
    '  bullets       — array of short benefit-driven bullets (5 for Amazon)',
    '  keywords      — array of relevant search keywords',
    '  suggested_price — number, in the given currency (or omit)',
    '  category      — short category string',
    'Return ONLY the JSON. No preamble, no trailing commentary.',
  ].filter(Boolean).join('\n')
}

function userMessageFor(body) {
  return [
    `Product description: ${body.description}`,
    body.product_name && `Product name: ${body.product_name}`,
    body.features && `Features: ${JSON.stringify(body.features)}`,
    body.price && `Seller target price: ${body.price} ${body.currency || 'USD'}`,
    body.category && `Category: ${body.category}`,
    body.brand && `Brand: ${body.brand}`,
    body.target_keywords && `Target keywords: ${JSON.stringify(body.target_keywords)}`,
    body.tags && `Tags (Etsy): ${JSON.stringify(body.tags)}`,
    body.materials && `Materials: ${JSON.stringify(body.materials)}`,
    body.target_region && `Target region: ${body.target_region}`,
  ].filter(Boolean).join('\n')
}

export async function generateListing(body) {
  const platform = body.platform || 'Generic'
  const system = systemPromptFor(platform)
  const userText = userMessageFor(body)

  // ═════════════════════════════════════════════════════════════════════════
  // TODO 3 — Invoke Bedrock Converse.
  //
  //   Use `new ConverseCommand({...})` with:
  //     - modelId: MODEL_ID (the global inference profile for Sonnet 4.6)
  //     - system: [{ text: system }]
  //     - messages: [{ role: 'user', content: [{ text: userText }] }]
  //     - inferenceConfig: { maxTokens: 2048, temperature: 0.5 }
  //
  //   Docs: https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html
  // ═════════════════════════════════════════════════════════════════════════

  // const resp = await bedrock.send(new ConverseCommand({
  //   ...
  // }))

  const blocks = resp.output?.message?.content ?? []
  const raw = blocks.map((b) => b.text ?? '').join('').trim()
  const listing = extractJson(raw)
  return {
    listing,
    usage: {
      inputTokens: resp.usage?.inputTokens,
      outputTokens: resp.usage?.outputTokens,
      modelId: MODEL_ID,
    },
  }
}

function extractJson(text) {
  try { return JSON.parse(text) } catch { /* fall through */ }
  const first = text.indexOf('{')
  const last = text.lastIndexOf('}')
  if (first !== -1 && last !== -1 && last > first) {
    try { return JSON.parse(text.slice(first, last + 1)) } catch { /* fall through */ }
  }
  return { raw: text, _parse_error: 'Could not parse model output as JSON' }
}
