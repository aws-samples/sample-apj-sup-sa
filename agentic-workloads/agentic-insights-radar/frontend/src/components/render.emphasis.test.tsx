// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Emphasis around a link or code (E2E F14: a bolded cited title showed its raw Markdown). */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import Markdown from './Markdown';

const show = (markdown: string) =>
  render(
    <MemoryRouter>
      <Markdown markdown={markdown} />
    </MemoryRouter>,
  );

describe('emphasis is parsed inside', () => {
  it('turns a bolded link into a bold link', () => {
    const { container } = show('See **[US: Section 301 tariffs](/insights/nodes/signal/abc)** first.');
    const link = screen.getByRole('link', { name: 'US: Section 301 tariffs' });
    expect(link).toHaveAttribute('href', '/insights/nodes/signal/abc');
    expect(link.closest('strong')).not.toBeNull();
    expect(container.textContent).toBe('See US: Section 301 tariffs first.');
  });

  it('keeps code and a link inside italics, and the text around them in place', () => {
    const { container } = show('_read `HTS 8517` and [the notice](/insights/nodes/fact/x)_ then **done**');
    expect(container.querySelector('em code')?.textContent).toBe('HTS 8517');
    expect(screen.getByRole('link', { name: 'the notice' }).closest('em')).not.toBeNull();
    expect(container.querySelector('strong')?.textContent).toBe('done');
    expect(container.textContent).toBe('read HTS 8517 and the notice then done');
  });

  it('parses emphasis inside a link label (E2E F15), but never a link inside a link', () => {
    const { container } = show('[**US: CBP voids IOR numbers**](/insights/nodes/signal/b) and [_see_ https://a.example/x](/insights/nodes/signal/c)');
    const bold = screen.getByRole('link', { name: 'US: CBP voids IOR numbers' });
    expect(bold.querySelector('strong')?.textContent).toBe('US: CBP voids IOR numbers');
    const plain = screen.getByRole('link', { name: '_see_ https://a.example/x' });
    expect(plain.querySelector('a, em')).toBeNull();
    expect(container.textContent).not.toContain('**');
  });

  it('links an address written with a capital scheme instead of turning it into italics', () => {
    const { container } = show('See HTTPS://www.dgft.gov.in/notifications for the list.');
    expect(container.querySelector('em')).toBeNull();
    expect(screen.getByRole('link', { name: /www\.dgft\.gov\.in/ })).toHaveAttribute('href', 'HTTPS://www.dgft.gov.in/notifications');
  });

  it('stays fast on a planted run of emphasis marks', () => {
    const planted = '**_*'.repeat(4000) + '[x](/a)';
    const t0 = performance.now();
    show(planted);
    expect(performance.now() - t0).toBeLessThan(2000);
  });
});
