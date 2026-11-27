// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import Markdown from './Markdown';
import SafeHtml from './SafeHtml';

const inRouter = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('SafeHtml', () => {
  it('keeps allowlisted markup and safe links', () => {
    const { container } = inRouter(
      <SafeHtml html='<p>One <strong>bold</strong> <em>i</em> <code>c</code><br><a href="https://x.example.com">ext</a> <a href="/insights/nodes/gap/1">int</a> <a href="#t">frag</a></p>' />,
    );
    expect(container.querySelector('strong')?.textContent).toBe('bold');
    const ext = screen.getByText('ext');
    expect(ext.getAttribute('target')).toBe('_blank');
    expect(ext.getAttribute('rel')).toBe('noopener noreferrer');
    expect(screen.getByText('int').getAttribute('href')).toBe('/insights/nodes/gap/1');
    expect(screen.getByText('frag').getAttribute('target')).toBeNull();
    expect(container.querySelector('br')).not.toBeNull();
  });

  it('drops scripts, handlers, unsafe and dead links', () => {
    const { container } = inRouter(
      <SafeHtml html='<p onclick="x()">t<script>alert(1)</script><img src=x onerror=alert(1)><a href="javascript:alert(1)">bad</a><a href="/radar/x/y">dead</a><div><span>kept</span></div><iframe src="x"></iframe></p>' />,
    );
    expect(container.innerHTML).not.toMatch(/script|onerror|onclick|iframe|<img|javascript/);
    expect(container.querySelector('a')).toBeNull();
    expect(container.textContent).toContain('bad');
    expect(container.textContent).toContain('kept');
  });

  it('drops comments and keeps the text of links without an address', () => {
    const { container } = inRouter(<SafeHtml html='<p>a<!-- hidden -->b <a>plain</a></p>' />);
    expect(container.innerHTML).not.toContain('hidden');
    expect(container.textContent).toBe('ab plain');
    expect(container.querySelector('a')).toBeNull();
  });

  it('renders nothing for empty input', () => {
    const { container } = inRouter(<SafeHtml html="" />);
    expect(container.innerHTML).toBe('');
  });
});

describe('Markdown', () => {
  it('renders the supported subset', () => {
    const md = [
      '# Title',
      '',
      'Some **bold**, *em*, `code`, a [link](https://example.com/a) and <a href="/insights/nodes/signal/1">internal</a>.',
      '',
      '- one',
      '- two',
      '',
      '1. first',
      '2. second',
      '',
      '| A | B |',
      '|---|:-:|',
      '| x | y \\| z |',
      '',
      '```',
      '<script>alert(1)</script>',
      '```',
      '![diagram](https://example.com/d.png)',
      '[bad](javascript:alert(1))',
    ].join('\n');
    const { container } = inRouter(<Markdown markdown={md} />);
    expect(container.querySelector('h2')?.textContent).toBe('Title');
    expect(container.querySelectorAll('ul li')).toHaveLength(2);
    expect(container.querySelectorAll('ol li')).toHaveLength(2);
    expect(container.querySelector('td:last-child')?.textContent).toBe('y | z');
    expect(container.querySelector('pre code')?.textContent).toBe('<script>alert(1)</script>');
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText('link').getAttribute('href')).toBe('https://example.com/a');
    expect(screen.getByText('internal').getAttribute('href')).toBe('/insights/nodes/signal/1');
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('[image: diagram]').tagName).toBe('A');
    expect(container.querySelector('a[href^="javascript"]')).toBeNull();
    expect(container.textContent).toContain('bad');
  });

  it('labels a link without text by its address and ends on a token', () => {
    inRouter(<Markdown markdown={'See [](https://example.com/p) and **end**'} />);
    expect(screen.getByText('https://example.com/p').getAttribute('href')).toBe('https://example.com/p');
    expect(screen.getByText('end').tagName).toBe('STRONG');
  });

  it('pads short table rows and keeps pipes in prose', () => {
    const { container } = inRouter(<Markdown markdown={['| A | B |', '|---|---|', '| only |', '', 'either | or', 'this | that'].join('\n')} />);
    expect([...container.querySelectorAll('td')].map((td) => td.textContent)).toEqual(['only', '']);
    expect(container.querySelector('p')?.textContent).toBe('either | or this | that');
  });

  it('degrades malformed anchors to text', () => {
    const { container } = inRouter(<Markdown markdown={'<a href="x">ok</a> and <a href=x>no</a>'} />);
    expect(container.textContent).toContain('<a href=x>no</a>');
  });

  describe('always terminates', () => {
    // A synchronous endless loop cannot be stopped by a test timeout, so cap the regex checks the
    // renderer makes per line: a regression throws here instead of freezing the run.
    const LIMIT = 200_000;
    afterEach(() => vi.restoreAllMocks());
    const guarded = (markdown: string) => {
      let calls = 0;
      const test = RegExp.prototype.test;
      vi.spyOn(RegExp.prototype, 'test').mockImplementation(function (this: RegExp, s: string) {
        if (++calls > LIMIT) throw new Error('Markdown did not terminate');
        return test.call(this, s);
      });
      return inRouter(<Markdown markdown={markdown} />);
    };

    it('treats CR, CRLF and the Unicode line and paragraph separators as line breaks', () => {
      const { container } = guarded('## Findings\r\nbody\r# Next\u2028text\u2029### Last\u2028');
      expect([...container.querySelectorAll('h3, h2, h4')].map((h) => `${h.tagName}:${h.textContent}`)).toEqual([
        'H3:Findings',
        'H2:Next',
        'H4:Last',
      ]);
      expect([...container.querySelectorAll('p')].map((p) => p.textContent)).toEqual(['body', 'text']);
    });

    it('ends on any mix of markup and line-break characters', () => {
      const parts = ['#', '## ', ' ', '\t', '\r', '\n', '\r\n', '\u2028', '\u2029', '- ', '1. ', '|', '---', '```', '**', 'a', 'x y'];
      let seed = 0x2545f491;
      const next = () => {
        seed ^= seed << 13;
        seed ^= seed >>> 17;
        seed ^= seed << 5;
        return (seed >>> 0) / 2 ** 32;
      };
      for (let run = 0; run < 300; run++) {
        const text = Array.from({ length: 1 + Math.floor(next() * 40) }, () => parts[Math.floor(next() * parts.length)]).join('');
        const { unmount } = guarded(text);
        unmount();
        vi.restoreAllMocks();
      }
    });
  });
});


describe('external links in agent output', () => {
  it('always show the real destination host', () => {
    render(
      <MemoryRouter>
        <Markdown markdown={'See [AWS pricing page](https://attacker.example/c) or [news.example.com](https://news.example.com/a).'} />
      </MemoryRouter>,
    );
    const evil = screen.getByRole('link', { name: /AWS pricing page/ });
    expect(evil.textContent).toBe('AWS pricing page (attacker.example)');
    expect(evil.getAttribute('title')).toBe('https://attacker.example/c');
    expect(screen.getByRole('link', { name: 'news.example.com' }).textContent).toBe('news.example.com');
  });
});

describe('Markdown on hostile input', () => {
  it('stays fast on a planted line of unclosed anchors', () => {
    const planted = '<a href="x" '.repeat(4000); // took 21 s at half this size with the old pattern
    const started = performance.now();
    render(<Markdown markdown={planted} />);
    expect(performance.now() - started).toBeLessThan(2000);
  });

  it.each([
    ['open brackets', '['.repeat(19_900)],
    ['empty links', '[]('.repeat(6_600)],
  ])('stays fast on a planted line of %s', (_, planted) => {
    // 480 ms and 130 ms in Node with the old link pattern, and re-parsed on every streamed chunk
    const started = performance.now();
    inRouter(<Markdown markdown={`${planted} [ok](https://example.com/a)`} />);
    expect(performance.now() - started).toBeLessThan(250);
    expect(screen.getByText('ok').getAttribute('href')).toBe('https://example.com/a');
  });

  it('renders an over-long line as plain text', () => {
    const { container } = render(<Markdown markdown={`**bold** ${'x'.repeat(20_001)}`} />);
    expect(container.querySelector('strong')).toBeNull();
  });
});
