/**
 * GET /openapi.json — service discovery document. Samurai's `discover_service`
 * tool fetches this at agent startup to learn:
 *   - what endpoints exist
 *   - what input schema each platform wants
 *   - how much /generate costs and on what network (x-payment-info)
 */
import { rules } from './validation.mjs'

const G = rules.global

function platformInputSchema(platform) {
  const block = rules[platform] ?? {}
  const requiredExtra = Array.isArray(block.required_extra) ? block.required_extra : []
  const optionalExtra = Array.isArray(block.optional_extra) ? block.optional_extra : []

  const properties = {
    description: {
      type: 'string',
      minLength: G.min_description_chars,
      maxLength: G.max_description_chars,
      description: 'Free-text product description from the seller.',
    },
    platform: {
      type: 'string',
      const: platform,
      description: 'Target marketplace.',
    },
    product_name: { type: 'string', description: 'Short product name / title seed.' },
    features: {
      type: 'array',
      items: { type: 'string' },
      description: `If provided, at least ${G.min_features_if_provided} items.`,
    },
    price: { type: 'number', exclusiveMinimum: 0 },
    currency: { type: 'string', default: 'USD' },
    category: { type: 'string' },
    // Platform extras
    brand: { type: 'string' },
    target_keywords: { type: 'array', items: { type: 'string' } },
    tags: { type: 'array', items: { type: 'string' } },
    materials: { type: 'array', items: { type: 'string' } },
    is_handmade: { type: 'boolean' },
    seo_keywords: { type: 'array', items: { type: 'string' } },
    variants: { type: 'array', items: { type: 'object' } },
    target_region: { type: 'string' },
  }

  return {
    type: 'object',
    required: [...G.required_input_fields, ...requiredExtra],
    properties,
    additionalProperties: false,
    'x-optional-fields': [...G.optional_input_fields, ...optionalExtra],
    'x-platform-guidance': block.guidance ?? '',
  }
}

export function buildOpenApiDocument({ baseUrl, price }) {
  const platforms = G.allowed_platforms
  const platformSchemas = Object.fromEntries(
    platforms.map((p) => [p, platformInputSchema(p)]),
  )

  return {
    openapi: '3.1.0',
    info: {
      title: 'ListingBot',
      version: '1.0.0',
      description:
        'Generates marketplace product listings. /generate is paid via Stripe MPP (SPT).',
    },
    servers: baseUrl ? [{ url: baseUrl }] : [],
    'x-payment-info': {
      protocol: 'MPP',
      method: 'stripe.charge',
      currency: 'USD',
      payment_method_types: ['card', 'link'],
      unit_price: price,
      price_scope: 'per_request',
      docs: 'https://docs.stripe.com/payments/machine/mpp?mpp-method=spt',
    },
    'x-platform-schemas': platformSchemas,
    paths: {
      '/openapi.json': {
        get: {
          summary: 'Service discovery document',
          description: 'Free. Returns this document — no payment required.',
          responses: { 200: { description: 'OK', content: { 'application/json': {} } } },
        },
      },
      '/validate': {
        post: {
          summary: 'Validate a /generate payload without generating',
          description:
            'Free. Same validation rules as the pre-payment gate on /generate. Use this to confirm input shape before spending.',
          requestBody: {
            required: true,
            content: { 'application/json': { schema: { $ref: '#/components/schemas/GeneratePayload' } } },
          },
          responses: {
            200: {
              description: 'Validation result',
              content: {
                'application/json': {
                  schema: {
                    type: 'object',
                    properties: {
                      valid: { type: 'boolean' },
                      errors: {
                        type: 'array',
                        items: {
                          type: 'object',
                          properties: {
                            field: { type: 'string' },
                            code: { type: 'string' },
                            message: { type: 'string' },
                          },
                        },
                      },
                    },
                  },
                },
              },
            },
          },
        },
      },
      '/generate': {
        post: {
          summary: 'Generate a marketplace listing (paid)',
          description:
            'Paid endpoint. Flow: 400 on malformed JSON, 422 RFC 7807 on invalid input (no payment), 402 with MPP challenge on missing/invalid credential, 200 with { listing, usage, sessionId } on success.',
          requestBody: {
            required: true,
            content: { 'application/json': { schema: { $ref: '#/components/schemas/GeneratePayload' } } },
          },
          responses: {
            200: { description: 'Generated listing' },
            400: { description: 'Malformed JSON' },
            402: {
              description: 'Payment required — MPP challenge',
              headers: {
                'WWW-Authenticate': {
                  description:
                    'MPP challenge. On API Gateway the server may mirror this header as x-amzn-remapped-www-authenticate — clients must read both.',
                  schema: { type: 'string' },
                },
              },
            },
            422: {
              description: 'Invalid input (RFC 7807 problem details). No payment taken.',
              content: {
                'application/problem+json': {
                  schema: { $ref: '#/components/schemas/ProblemDetails' },
                },
              },
            },
          },
        },
      },
    },
    components: {
      schemas: {
        GeneratePayload: {
          oneOf: platforms.map((p) => ({ $ref: `#/components/schemas/${p}Payload` })),
        },
        ...Object.fromEntries(platforms.map((p) => [`${p}Payload`, platformSchemas[p]])),
        ProblemDetails: {
          type: 'object',
          properties: {
            type: { type: 'string', format: 'uri' },
            title: { type: 'string' },
            status: { type: 'integer' },
            detail: { type: 'string' },
            errors: {
              type: 'array',
              items: {
                type: 'object',
                properties: {
                  field: { type: 'string' },
                  code: { type: 'string' },
                  message: { type: 'string' },
                },
              },
            },
          },
        },
      },
    },
  }
}
