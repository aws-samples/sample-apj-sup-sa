/**
 * Shared input validation. Used by:
 *   - POST /validate (free) — returns the error list verbatim
 *   - POST /generate (paid) — runs this BEFORE minting a PaymentIntent, so
 *     the caller is never charged for invalid input. Maps to 422 RFC 7807.
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
export const rules = JSON.parse(readFileSync(join(__dirname, 'rules.json'), 'utf8'))

export function validateInput(body) {
  const errors = []
  const G = rules.global
  body = body ?? {}

  for (const f of G.required_input_fields) {
    if (body[f] === undefined || body[f] === null || body[f] === '') {
      errors.push({ field: f, code: 'missing', message: `${f} is required` })
    }
  }

  if (typeof body.platform === 'string' && !G.allowed_platforms.includes(body.platform)) {
    errors.push({
      field: 'platform',
      code: 'enum',
      message: `platform must be one of ${G.allowed_platforms.join(', ')}`,
    })
  }

  if (body.description !== undefined) {
    if (typeof body.description !== 'string') {
      errors.push({ field: 'description', code: 'type', message: 'description must be a string' })
    } else {
      if (body.description.length < G.min_description_chars) {
        errors.push({
          field: 'description', code: 'too_short',
          message: `description must be at least ${G.min_description_chars} chars`,
        })
      }
      if (body.description.length > G.max_description_chars) {
        errors.push({
          field: 'description', code: 'too_long',
          message: `description must be at most ${G.max_description_chars} chars`,
        })
      }
      const lower = body.description.toLowerCase()
      for (const phrase of G.forbidden_phrases) {
        if (lower.includes(phrase)) {
          errors.push({
            field: 'description', code: 'forbidden_phrase',
            message: `description contains forbidden phrase "${phrase}"`,
          })
        }
      }
    }
  }

  if (body.features !== undefined) {
    if (!Array.isArray(body.features)) {
      errors.push({ field: 'features', code: 'type', message: 'features must be an array' })
    } else if (body.features.length > 0 && body.features.length < G.min_features_if_provided) {
      errors.push({
        field: 'features', code: 'too_few',
        message: `if provided, features must have at least ${G.min_features_if_provided} items`,
      })
    }
  }

  if (body.price !== undefined) {
    const n = Number(body.price)
    if (!Number.isFinite(n) || n <= 0) {
      errors.push({ field: 'price', code: 'invalid', message: 'price must be a positive number' })
    }
  }

  const platformBlock = typeof body.platform === 'string' ? rules[body.platform] : null
  if (platformBlock && Array.isArray(platformBlock.required_extra)) {
    for (const f of platformBlock.required_extra) {
      if (body[f] === undefined || body[f] === null || body[f] === '') {
        errors.push({
          field: f, code: 'missing',
          message: `${f} is required for platform ${body.platform}`,
        })
      }
    }
  }

  return { valid: errors.length === 0, errors }
}
