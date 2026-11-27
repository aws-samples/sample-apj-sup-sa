// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Mark web-sourced text as untrusted before it reaches the user's assistant.
 *
 * Graph entries, evidence and radar descriptions were written by agents from crawled pages, so they can
 * carry instructions aimed at a model. Every tool result is wrapped in the same `untrusted-tool-output`
 * fence the solution's own agents use, with any fence or context marker inside it neutralised so the
 * text cannot close the fence early. The server instructions tell the assistant to treat fenced text as
 * data. It finds markers the way `insights_radar.core.defang.Defang.fence` does, so the MCP server
 * installs without the backend package.
 *
 * Unlike `Defang`, it does not hand back the folded text: markers are looked for in an NFKC-folded copy
 * with every invisible format character removed, and only the matched spans of the original are
 * replaced. Everything else reaches the assistant exactly as the API sent it, so an `export_radar` CSV
 * or JSON keeps full-width and superscript characters and the ZWJ and ZWNJ of emoji and of Persian or
 * Hindi names, and re-imports as the same entries (A3-041).
 *
 * Lengths are counted in code points, as the Python server counted them.
 */

import { cpLen, cpSlice, isObject, pyJson } from './py.js';

export const FENCE_TAG = 'untrusted-tool-output';
export const MARKER_REDACTED = '\u27ea redacted-marker \u27eb';

const ZERO_WIDTH = /^[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]$/u;
const FORMAT = /\p{Cf}/gu;
// Python's \s: JavaScript's, plus U+001C..U+001F and U+0085.
const WS = '\\s\\x1c-\\x1f\\x85';
const DASH = `[-_\\u2010-\\u2015\\u2212\\ufe58\\ufe63\\uff0d${WS}]*`;
const FENCE_RE = `<[${WS}]*/?[${WS}]*untrusted${DASH}tool${DASH}output[^>]*>?`;
const CONTEXT_MARKER_RE = `\\[\\[?[${WS}]*context[${WS}]*:?`;
const MARKERS = new RegExp(`${FENCE_RE}|${CONTEXT_MARKER_RE}`, 'giu');
const ASCII = /^[^\u0080-\uffff]*$/;

/** One character as marker detection reads it: NFKC, with every invisible format character gone. */
function fold(char: string): string {
  if (ZERO_WIDTH.test(char)) return '';
  return char.normalize('NFKC').replace(FORMAT, '');
}

const HTML_ESCAPES: Record<string, string> = { '<': '\\u003c', '>': '\\u003e', '&': '\\u0026' };

export class Fence {
  static redactMarkers(text: string): string {
    if (ASCII.test(text)) {
      // ASCII folds to itself, so the spans found are the original's.
      return text.replace(MARKERS, MARKER_REDACTED);
    }
    const chars = Array.from(text);
    let folded = '';
    const origin: number[] = []; // for each UTF-16 unit of the folded text, the original code point
    chars.forEach((char, i) => {
      const piece = fold(char);
      folded += piece;
      for (let k = 0; k < piece.length; k++) origin.push(i);
    });
    const out: string[] = [];
    let last = 0;
    for (const match of folded.matchAll(MARKERS)) {
      const start = origin[match.index];
      const end = origin[match.index + match[0].length - 1] + 1;
      out.push(chars.slice(last, start).join(''), MARKER_REDACTED);
      last = end;
    }
    out.push(chars.slice(last).join(''));
    return out.join('');
  }

  /**
   * User- and agent-written text (titles, names, snippets, error messages, evidence URLs) would reach a
   * client that renders results as Markdown or HTML as a literal '<img onerror=...>'. These JSON escapes
   * parse back to the same value; they run after redaction so markers are still found (CTR
   * HC-145/146/147/144/142b, QA-025).
   */
  static jsonBody(payload: string): string {
    return Fence.redactMarkers(payload).replace(/[<>&]/g, (c) => HTML_ESCAPES[c]);
  }

  /**
   * A JSON result over the limit, made to fit and still parse (CTR HC-121): cutting the text at the
   * limit left a client half an object it could not read. The longest list is shortened, with a
   * `truncated` note saying how many items are shown of how many; then long strings are cut; and a
   * result that still does not fit is replaced by a note.
   */
  static fitJson(payload: string, maxLen: number): string {
    const size = (value: unknown): number => cpLen(Fence.jsonBody(pyJson(value)));
    let value: unknown;
    try {
      value = JSON.parse(payload);
    } catch {
      value = null;
    }
    if (isObject(value)) {
      const lists = Object.entries(value).filter(
        (kv): kv is [string, unknown[]] => Array.isArray(kv[1]) && kv[1].length > 0,
      );
      if (lists.length) {
        let [key, items] = lists[0];
        let biggest = size(items);
        for (const [k, v] of lists.slice(1)) {
          const s = size(v);
          if (s > biggest) [key, items, biggest] = [k, v, s];
        }
        const trial = (shown: number): Record<string, unknown> => ({
          ...(value as Record<string, unknown>),
          [key]: items.slice(0, shown),
          truncated: { field: key, shown, total: items.length },
        });
        let low = 0;
        let high = items.length;
        while (low < high) {
          // the most items that fit
          const mid = Math.floor((low + high + 1) / 2);
          if (size(trial(mid)) <= maxLen) low = mid;
          else high = mid - 1;
        }
        value = trial(low);
        if (size(value) <= maxLen) return Fence.jsonBody(pyJson(value));
      }
      value = cutStrings(value, 2000);
      if (size(value) <= maxLen) return Fence.jsonBody(pyJson(value));
    }
    const note = {
      truncated: true,
      note: 'the result was too large to return; narrow the request (a smaller limit or a filter)',
    };
    return Fence.jsonBody(pyJson(note));
  }

  static wrap(tool: string, payload: string, maxLen: number, { jsonPayload = false } = {}): string {
    let body: string;
    if (jsonPayload) {
      body = Fence.jsonBody(payload);
      if (cpLen(body) > maxLen) body = Fence.fitJson(payload, maxLen);
    } else {
      body = Fence.redactMarkers(payload);
      if (cpLen(body) > maxLen) body = cpSlice(body, maxLen) + '\n[truncated]';
    }
    const safeTool = cpSlice(Array.from(tool, (c) => (/^[A-Za-z0-9_.-]$/.test(c) ? c : '_')).join(''), 64);
    return `<${FENCE_TAG} tool="${safeTool}">\n${body}\n</${FENCE_TAG}>`;
  }
}

/** `value` with every string longer than `limit` cut to it (and marked with an ellipsis). */
function cutStrings(value: unknown, limit: number): unknown {
  if (typeof value === 'string') return cpLen(value) <= limit ? value : cpSlice(value, limit) + '\u2026';
  if (Array.isArray(value)) return value.map((v) => cutStrings(v, limit));
  if (isObject(value)) return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, cutStrings(v, limit)]));
  return value;
}
