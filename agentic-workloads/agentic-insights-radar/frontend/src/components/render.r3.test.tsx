// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Round-3 red-team findings about rendering model text (F3-022, F3-048). */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import Markdown from './Markdown';
import SafeHtml from './SafeHtml';

describe('F3-022: one rule for every external link from model text', () => {
  it('keeps a Markdown link or an <a href> with a query or fragment as text that shows the full address', () => {
    const { container } = render(
      <MemoryRouter>
        <Markdown markdown={'[x](https://c.example/c?d=secret) and <a href="https://c.example/p#frag">y</a> and [ok](https://ok.example/a)'} />
      </MemoryRouter>,
    );
    expect(container.querySelectorAll('a')).toHaveLength(1);
    expect(screen.getByRole('link', { name: /ok/ })).toHaveAttribute('href', 'https://ok.example/a');
    expect(container.textContent).toContain('x (https://c.example/c?d=secret)');
    expect(container.textContent).toContain('y (https://c.example/p#frag)');
  });

  it('applies to sanitised HTML too, but leaves in-app links alone', () => {
    const { container } = render(
      <MemoryRouter>
        <SafeHtml html={'<p><a href="https://c.example/c?d=1">evil</a> <a href="/radars?q=x">radars</a></p>'} />
      </MemoryRouter>,
    );
    expect(container.querySelectorAll('a')).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'radars' })).toHaveAttribute('href', '/radars?q=x');
    expect(container.textContent).toContain('evil (https://c.example/c?d=1)');
  });
});

describe('F3-048: an address that ends in a parenthesis', () => {
  it('links the whole Wikipedia address', () => {
    const { container } = render(
      <MemoryRouter>
        <Markdown markdown={'See https://en.wikipedia.org/wiki/Foo_(bar) and https://en.wikipedia.org/wiki/Baz_(qux)x, too.'} />
      </MemoryRouter>,
    );
    const links = [...container.querySelectorAll('a')].map((a) => a.getAttribute('href'));
    expect(links).toEqual(['https://en.wikipedia.org/wiki/Foo_(bar)', 'https://en.wikipedia.org/wiki/Baz_(qux)x']);
  });
});
