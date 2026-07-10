/**
 * ListingDialog — modal that renders a generated listing JSON as a
 * readable product card (title, price, description, bullets, keywords,
 * meta description).
 *
 * Used from AgentRunner's "View as listing" button on assistant bubbles
 * that contain a listing payload. The bubble itself shows the raw JSON
 * (the educational artifact — what the paid /generate call returned over
 * the wire); this dialog is the human-readable secondary view.
 *
 * Normalises across field-name variants the model occasionally emits:
 *   bullets    | bullet_points | key_features
 *   keywords   | seo_keywords
 *   description           (string or null)
 *   meta_description      (string or null)
 *   suggested_price       (string, or { amount, currency, rationale })
 *
 * Ported from /Users/qinjie/Code/stripe-sample-application-fiat/src/frontend
 * /src/components/ListingDialog.tsx — same semantics, JSX flavor.
 */
import * as Dialog from '@radix-ui/react-dialog'
import { useState } from 'react'

// ----- coercion helpers -----------------------------------------------------

function asString(v) {
  if (typeof v === 'string' && v.trim()) return v
  return null
}

function asStringArray(v) {
  if (!Array.isArray(v)) return []
  return v.map((item) => {
    if (typeof item === 'string') return item
    if (item && typeof item === 'object') {
      for (const k of ['text', 'label', 'value', 'description']) {
        if (typeof item[k] === 'string') return item[k]
      }
      return JSON.stringify(item)
    }
    return String(item)
  })
}

function formatPrice(p) {
  if (!p) return null
  if (typeof p === 'string') return p.trim() || null
  if (typeof p === 'object') {
    const { amount, currency } = p
    if (amount != null && currency) return `${currency} ${amount}`
    if (amount != null) return String(amount)
  }
  return null
}

function formatPriceRationale(p) {
  if (!p || typeof p !== 'object') return null
  return asString(p.rationale)
}

// ----- component ------------------------------------------------------------

export default function ListingDialog({ open, onOpenChange, listing }) {
  const [copied, setCopied] = useState('')

  const title = asString(listing.title)
  const description = asString(listing.description)
  const metaDescription = asString(listing.meta_description)
  const priceText = formatPrice(listing.suggested_price)
  const priceRationale = formatPriceRationale(listing.suggested_price)
  const category = asString(listing.category)

  // Accept any of the model's naming conventions.
  const bullets = asStringArray(
    listing.bullet_points ?? listing.bullets ?? listing.key_features,
  )
  const keywords = asStringArray(listing.seo_keywords ?? listing.keywords)

  const copyAsText = async () => {
    const lines = []
    if (title) lines.push(`Title: ${title}`, '')
    if (priceText) lines.push(`Price: ${priceText}`, '')
    if (category) lines.push(`Category: ${category}`, '')
    if (description) lines.push('Description:', description, '')
    if (bullets.length > 0) {
      lines.push('Bullet points:')
      for (const b of bullets) lines.push(`  • ${b}`)
      lines.push('')
    }
    if (keywords.length > 0) {
      lines.push(`Keywords: ${keywords.join(', ')}`, '')
    }
    if (metaDescription) {
      lines.push('Meta description:', metaDescription, '')
    }
    await navigator.clipboard.writeText(lines.join('\n').trim())
    setCopied('text')
    setTimeout(() => setCopied(''), 1200)
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50 z-40" />
        <Dialog.Content
          className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[min(90vw,720px)] max-h-[85vh] overflow-y-auto rounded-xl bg-white shadow-2xl"
          aria-describedby={undefined}
        >
          <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between gap-4 rounded-t-xl">
            <Dialog.Title className="text-lg font-semibold text-gray-900">
              Listing details
            </Dialog.Title>
            <div className="flex items-center gap-2">
              <button
                onClick={copyAsText}
                className="text-xs bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-md px-3 py-1.5 font-medium transition-colors"
              >
                {copied === 'text' ? '✓ Copied!' : 'Copy as text'}
              </button>
              <Dialog.Close asChild>
                <button
                  className="text-gray-400 hover:text-gray-600"
                  aria-label="Close"
                >
                  <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                  </svg>
                </button>
              </Dialog.Close>
            </div>
          </div>

          <div className="px-6 py-5 space-y-5">
            {title && (
              <header className="space-y-1">
                <h2 className="text-xl font-semibold text-gray-900 leading-snug">
                  {title}
                </h2>
                <div className="flex items-center gap-3 flex-wrap">
                  {priceText && (
                    <span className="bg-emerald-50 text-emerald-700 text-sm font-semibold px-3 py-1 rounded-full">
                      {priceText}
                    </span>
                  )}
                  {category && (
                    <span className="text-xs text-gray-500 uppercase tracking-wide">
                      {category}
                    </span>
                  )}
                </div>
                {priceRationale && (
                  <p className="text-xs text-gray-500 italic">{priceRationale}</p>
                )}
              </header>
            )}

            {description && (
              <section>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  Description
                </h3>
                <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">
                  {description}
                </p>
              </section>
            )}

            {bullets.length > 0 && (
              <section>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  Bullet points
                </h3>
                <ul className="space-y-1.5">
                  {bullets.map((b, i) => (
                    <li key={i} className="flex items-start text-sm text-gray-700">
                      <span className="text-emerald-600 mr-2 mt-0.5 shrink-0">✓</span>
                      <span>{b}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {keywords.length > 0 && (
              <section>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  Keywords
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {keywords.map((k, i) => (
                    <span
                      key={i}
                      className="inline-block text-xs bg-gray-100 text-gray-700 px-2.5 py-1 rounded-full"
                    >
                      {k}
                    </span>
                  ))}
                </div>
              </section>
            )}

            {metaDescription && (
              <section>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  Meta description
                </h3>
                <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">
                  {metaDescription}
                </p>
              </section>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
