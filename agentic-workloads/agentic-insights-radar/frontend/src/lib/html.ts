// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Link safety, mirroring the backend's HtmlSanitizer: the server sanitises every description,
 * and the browser checks every href again before rendering a link.
 */
export const ALLOWED_TAGS: ReadonlySet<string> = new Set(['p', 'br', 'strong', 'em', 'a', 'code']);

const SAFE_SCHEME = /^(https?:|mailto:)/i;
const ANY_SCHEME = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;
// Controls, whitespace, DEL, C1 controls, U+FFFD and Unicode format characters (zero-width, bidi).
// eslint-disable-next-line no-control-regex -- control characters are exactly what this strips
const INVISIBLE = /[\u0000-\u0020\u007f-\u009f\ufffd\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]/g;

/** The href as a browser URL parser sees it when choosing the scheme. */
export function canonicalHref(href: string): string {
  let decoded = href;
  if (typeof document !== 'undefined' && /&/.test(href)) {
    const textarea = document.createElement('textarea');
    textarea.innerHTML = href; // entity decoding only: a textarea never parses markup
    decoded = textarea.value;
  }
  return decoded.replace(INVISIBLE, '');
}

export function isSafeHref(href: string): boolean {
  const canon = canonicalHref(href);
  if (!canon) return false;
  if (/^[/\\]{2}/.test(canon) || /^\/\\/.test(canon)) return false; // protocol-relative and backslash tricks
  if (SAFE_SCHEME.test(canon)) return true;
  return !ANY_SCHEME.test(canon);
}

/** Routes that exist in the app (App.tsx); internal links elsewhere would be dead. */
const ROUTES = new Set(['', 'insights', 'radars', 'graph', 'products', 'modules', 'admin']);

export function resolvesInApp(href: string): boolean {
  const path = href.replace(/[?#][\s\S]*$/, ''); // up to the query or fragment
  const first = path.split('/').filter(Boolean)[0] ?? '';
  return ROUTES.has(first);
}

/**
 * A stored description as the text a person typed. The API stores entry descriptions as sanitised
 * HTML, so "R&D" comes back as "R&amp;D", and the entry panel, the editor and the CSV showed the
 * entity (r3 F3-026). Line breaks the markup carries (<br>, a paragraph end) become newlines; every
 * other tag is dropped. A plain text without markup or entities is returned as it is.
 */
export function storedText(html: string): string {
  if (!/[<&]/.test(html)) return html;
  const broken = html.replace(/<br\s*\/?>/gi, '\n').replace(/<\/p>\s*/gi, '\n');
  const doc = new DOMParser().parseFromString(broken, 'text/html'); // inert: nothing in it runs
  return String(doc.body.textContent).replace(/\n+$/, '');
}
