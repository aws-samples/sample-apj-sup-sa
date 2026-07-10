/**
 * Listing-Bot Lambda — router. Fronted by API Gateway REST API.
 *
 * Routes:
 *   GET  /openapi.json  — free service discovery (Samurai fetches at startup)
 *   POST /validate       — free deterministic input check
 *   POST /generate       — paid (MPP-gated) listing generation
 */
import { handleValidate } from './validate.mjs'
import { handleGenerate } from './generate.mjs'
import { buildOpenApiDocument } from './openapi-spec.mjs'

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type,Authorization,X-User-Id,X-Session-Id',
  'Access-Control-Expose-Headers':
    'WWW-Authenticate,Payment-Receipt,X-Payment-Intent-Id,x-amzn-remapped-www-authenticate',
}

export async function handler(event) {
  const method = event.httpMethod || event.requestContext?.http?.method || 'GET'
  const path = event.path || event.rawPath || '/'

  if (method === 'OPTIONS') {
    return { statusCode: 204, headers: CORS_HEADERS, body: '' }
  }

  try {
    if (path === '/openapi.json' && method === 'GET') {
      const baseUrl = baseUrlFromEvent(event)
      const doc = buildOpenApiDocument({
        baseUrl,
        price: process.env.LISTING_PRICE || '1.00',
      })
      return json(200, doc)
    }
    if (path === '/validate' && method === 'POST') {
      return json(200, handleValidate(parseBody(event)))
    }
    if (path === '/generate' && method === 'POST') {
      return await handleGenerate(event)
    }
    return json(404, { error: 'not_found', path, method })
  } catch (err) {
    console.error('Unhandled error', err)
    return json(500, { error: 'internal_error', message: err?.message ?? 'unknown' })
  }
}

function parseBody(event) {
  if (!event.body) return {}
  try { return JSON.parse(event.body) } catch { return {} }
}

function baseUrlFromEvent(event) {
  const headers = event.headers || {}
  const host = headers.host || headers.Host
  const proto = headers['x-forwarded-proto'] || headers['X-Forwarded-Proto'] || 'https'
  const stage = event.requestContext?.stage ? `/${event.requestContext.stage}` : ''
  return host ? `${proto}://${host}${stage}` : undefined
}

function json(statusCode, obj, extraHeaders = {}) {
  return {
    statusCode,
    headers: { 'Content-Type': 'application/json', ...CORS_HEADERS, ...extraHeaders },
    body: JSON.stringify(obj),
  }
}
