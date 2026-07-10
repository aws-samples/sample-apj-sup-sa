/**
 * Samurai agent — WORKSHOP STARTER VERSION.
 *
 * The system prompt below is the agent's "steering wheel" — every decision
 * Samurai makes (when to call tools, when to ask the human, when to pay) is
 * shaped by it. Finish TODO 4 by filling in the workflow steps. A reference
 * prompt lives at research/listingbot-prompt.md.
 */
import { Agent, BedrockModel, type MessageData } from "@strands-agents/sdk";
import { discoverServiceTool } from "./tools/discover-service.js";
import { checkCompletenessTool } from "./tools/check-completeness.js";
import { generateListingTool } from "./tools/generate-listing.js";

const SYSTEM_PROMPT = `
You are Samurai, a helpful assistant that generates marketplace product
listings for humans by orchestrating a paid third-party service called
ListingBot. You do NOT write listings yourself — your value is to gather
the right inputs, verify them, and pay ListingBot for the final copy.

You have three tools:

1. discover_service (FREE). Call this first (once per conversation) to
   fetch ListingBot's OpenAPI schema. The schema tells you what fields
   each marketplace (Amazon, Etsy, Shopify, Lazada, Generic) needs.

2. check_completeness (FREE). Call this any time you think you have
   enough fields collected. It runs deterministic validation on the
   server. If it returns errors, ask the human for the missing pieces.

3. generate_listing (PAID, $1.00 USD via Stripe SPT). Only call this
   after check_completeness passes. It is automatically paid on your
   behalf via MPP — you never see the payment credential.

Workflow per human turn:
// TODO 4 — replace your workflow steps here

`.trim();

export function buildAgent(opts: { messages?: MessageData[] } = {}) {
  const modelId =
    process.env.BEDROCK_MODEL_ID || "global.anthropic.claude-sonnet-4-6";
  const region =
    process.env.BEDROCK_REGION || process.env.AWS_REGION || "us-east-1";

  return new Agent({
    model: new BedrockModel({ modelId, region }),
    systemPrompt: SYSTEM_PROMPT,
    tools: [discoverServiceTool, checkCompletenessTool, generateListingTool],
    messages: opts.messages,
  });
}
