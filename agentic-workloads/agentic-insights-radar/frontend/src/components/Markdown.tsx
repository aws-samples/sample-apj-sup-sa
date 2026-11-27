// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import React from 'react';

import { safeAnchor } from './SafeHtml';

/**
 * Small, CSP-safe Markdown renderer for chat answers, briefs and drafts: headings, paragraphs,
 * bold/italic/code, fenced code, lists, GFM tables and links (Markdown or inline <a> anchors),
 * all through the same link-safety rules as SafeHtml. Unknown constructs render as text; raw
 * HTML is never interpreted. Images render as a labelled link (no remote image loading).
 */
// `_emphasis_` only between non-word characters, so snake_case words stay as they are.
// A bare https URL is a token too: a chat answer that cites one should be clickable, not text to copy.
// An address may hold one balanced pair of parentheses (Wikipedia titles do), and nothing deeper: a
// nested scan is what makes a planted run of "((((" cost quadratic time. After that pair the address
// may end at once: "Foo_(bar)" linked to "Foo_" (r3 F3-048).
const INLINE = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|(?<![\w])_[^_\s][^_]*_(?![\w])|`[^`]+`|!?\[[^[\]]*\]\((?:[^()\s]|\([^()\s]*\))+\)|<a\s+href="[^"<>]*"(?:\s+[a-z-]+="[^"<>]*")*\s*>[^<]*<\/a>|https:\/\/[^\s<>()\]]+(?:\([^\s()]*\)[^\s<>()\].,;:!?]?|[^\s<>()\].,;:!?])|https:\/\/[^\s<>()\].,;:!?]+)/gi;
// The same shapes as in INLINE, so a token always matches them; `replace` reads their groups as strings.
// Neither the label nor the address may contain a bracket of its own kind: otherwise every `[` starts a
// scan to the end of the line and a planted run of `[` or `[](` costs quadratic time.
const LINK = /^!?\[([^[\]]*)\]\(((?:[^()\s]|\([^()\s]*\))+)\)$/;
// href first, then quoted attributes: no two parts can match the same text, so matching stays linear
// (the looser `[^>]*href[^>]*` form backtracked cubically on a planted line of unclosed anchors).
const ANCHOR = /^<a\s+href="([^"<>]*)"(?:\s+[a-z-]+="[^"<>]*")*\s*>([^<]*)<\/a>$/i;
// Longer lines render as plain text: inline parsing is linear, but chat re-parses on every chunk.
const MAX_INLINE_CHARS = 20_000;
const HEADING = /^(#{1,6})\s+(.*)$/;
// `.` in HEADING does not match these, so they are normalised to `\n` before the text is split.
const LINE_BREAKS = /\r\n|\r|\u2028|\u2029/g;
const LIST = /^\s*([-*+]|\d+\.)\s+/;
const ITEM = /^(\s*)([-*+]|\d+\.)\s+([\s\S]*)$/;
// Three or more of the same mark, alone on a line (spaces allowed): a thematic break.
const RULE = /^ {0,3}([-*_])(?: *\1){2,} *$/;
const QUOTE = /^ {0,3}> ?/;

// A label with emphasis or code in it ("[**Title**](/x)") is parsed too, so the marks do not show. A label
// holding an address or an anchor stays text: parsed, it would put a link inside the link. A plain label
// stays a string, which safeAnchor compares with the link's host.
function linkText(label: string): React.ReactNode {
  return /[*_`]/.test(label) && !/https:\/\/|<a\s/i.test(label) ? inline(label) : label;
}

function inline(text: string): React.ReactNode[] {
  if (text.length > MAX_INLINE_CHARS) return [text];
  const out: React.ReactNode[] = [];
  let last = 0;
  let k = 0;
  // Its own copy: emphasis parses its content with a nested call, which must not move this loop's place.
  const re = new RegExp(INLINE);
  for (let m = re.exec(text); m !== null; m = re.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    // Emphasis content is parsed again, so a link or code inside bold stays a link: models often bold a
    // cited title. The content holds no `*`, so each level is shorter than the last.
    if (tok.startsWith('**')) out.push(<strong key={k++}>{inline(tok.slice(2, -2))}</strong>);
    else if (tok.startsWith('`')) out.push(<code key={k++}>{tok.slice(1, -1)}</code>);
    else if (tok.startsWith('![') || tok.startsWith('[')) {
      const href = tok.replace(LINK, '$2');
      const label = tok.replace(LINK, '$1') || href;
      out.push(safeAnchor(href, tok.startsWith('!') ? `[image: ${label}]` : linkText(label), `l${k++}`));
    } else if (/^<a\s/i.test(tok)) {
      out.push(safeAnchor(tok.replace(ANCHOR, '$1').trim(), tok.replace(ANCHOR, '$2'), `a${k++}`));
    } else if (/^https:\/\//i.test(tok)) { // INLINE matches the scheme in any case
      // A bare address is model-written text that nobody formatted as a link. With a query string or
      // a fragment it stays text (SR2-F01); safeAnchor holds every external link to the same rule.
      out.push(/[?#]/.test(tok) ? tok : safeAnchor(tok, tok, `u${k++}`));
    } else out.push(<em key={k++}>{inline(tok.slice(1, -1))}</em>); // *em* or _em_
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const isRow = (line: string): boolean => line.includes('|') && !line.trim().startsWith('```');
const cells = (line: string): string[] =>
  line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split(/(?<!\\)\|/)
    .map((c) => c.replace(/\\\|/g, '|').trim());
const isSeparator = (line: string): boolean => {
  const c = cells(line);
  return c.length > 0 && c.every((x) => /^:?-+:?$/.test(x));
};

/**
 * A list starting at line `from`: items at its indent, and deeper-indented items as a list nested in
 * the item before them. An ordered list keeps the number it starts at. Returns the line after it.
 */
function parseList(lines: string[], from: number): [React.ReactElement, number] {
  const head = ITEM.exec(lines[from] as string) as RegExpExecArray;
  const indent = (head[1] as string).length;
  const marker = head[2] as string;
  const ordered = /\d/.test(marker);
  const items: { text: string; children: React.ReactElement[] }[] = [];
  let i = from;
  while (i < lines.length) {
    const m = ITEM.exec(lines[i] as string);
    if (!m) break;
    const depth = (m[1] as string).length;
    if (depth < indent) break;
    if (depth > indent) {
      const [sub, next] = parseList(lines, i);
      (items[items.length - 1] as { children: React.ReactElement[] }).children.push(sub);
      i = next;
      continue;
    }
    items.push({ text: m[3] as string, children: [] });
    i++;
  }
  const lis = items.map((it, n) => (
    <li key={n}>
      {inline(it.text)}
      {it.children.map((c, k) => <React.Fragment key={k}>{c}</React.Fragment>)}
    </li>
  ));
  const start = Number.parseInt(marker, 10);
  return [ordered ? <ol start={start}>{lis}</ol> : <ul>{lis}</ul>, i];
}

/** Deeper quotes are shown as text: each level renders the rest recursively, and a line of thousands of
 * ">" (agent-written text can hold anything) would otherwise overflow the stack and blank the view. */
export const MAX_QUOTE_DEPTH = 8;

function render(markdown: string, depth = 0): React.ReactElement {
  const blocks: React.ReactNode[] = [];
  const lines = markdown.replace(LINE_BREAKS, '\n').split('\n');
  let i = 0;
  let key = 0;
  const at = (n: number): string => lines[n] ?? '';
  while (i < lines.length) {
    const line = at(i);
    if (line.startsWith('```')) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !at(i).startsWith('```')) code.push(at(i++));
      i++;
      blocks.push(
        <pre key={key++} className="air-code">
          <code>{code.join('\n')}</code>
        </pre>,
      );
      continue;
    }
    if (HEADING.test(line)) {
      const level = Math.min(line.replace(HEADING, '$1').length + 1, 6);
      blocks.push(React.createElement(`h${level}`, { key: key++ }, ...inline(line.replace(HEADING, '$2'))));
      i++;
      continue;
    }
    if (RULE.test(line)) {
      blocks.push(<hr key={key++} />);
      i++;
      continue;
    }
    if (QUOTE.test(line)) {
      const quoted: string[] = [];
      while (i < lines.length && QUOTE.test(at(i))) quoted.push(at(i++).replace(QUOTE, ''));
      const inner = quoted.join('\n');
      blocks.push(<blockquote key={key++}>{depth < MAX_QUOTE_DEPTH ? render(inner, depth + 1) : <p>{inner}</p>}</blockquote>);
      continue;
    }
    if (LIST.test(line)) {
      const [list, next] = parseList(lines, i);
      blocks.push(<React.Fragment key={key++}>{list}</React.Fragment>);
      i = next;
      continue;
    }
    if (isRow(line) && isSeparator(at(i + 1))) {
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && isRow(at(i)) && at(i).trim()) rows.push(cells(at(i++)));
      blocks.push(
        <div key={key++} className="air-table-wrap">
          <table className="air-table">
            <thead>
              <tr>{head.map((c, ci) => <th key={ci}>{inline(c)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>{head.map((_, ci) => <td key={ci}>{inline(r[ci] ?? '')}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    // The first line is always consumed, so the outer loop advances even if a check above and the
    // stop conditions below ever disagree about a line (that once froze the tab).
    const para: string[] = [at(i++)];
    while (
      i < lines.length &&
      at(i).trim() &&
      !at(i).startsWith('```') &&
      !HEADING.test(at(i)) &&
      !LIST.test(at(i)) &&
      !RULE.test(at(i)) &&
      !QUOTE.test(at(i)) &&
      !(isRow(at(i)) && isSeparator(at(i + 1)))
    )
      para.push(at(i++));
    blocks.push(<p key={key++}>{inline(para.join(' '))}</p>);
  }
  return <div className="air-markdown">{blocks}</div>;
}

/** Parsed once per text: the chat panel re-renders on every streamed chunk. */
export default function Markdown({ markdown }: { markdown: string }): React.ReactElement {
  return React.useMemo(() => render(markdown), [markdown]);
}
