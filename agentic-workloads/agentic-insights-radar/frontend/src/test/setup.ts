// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import '@testing-library/jest-dom/vitest';
import { configure } from '@testing-library/react';

// The first render of a page in a file loads it cold, which takes over the 1 s default under a busy full run:
// the gates failed at random on findBy* in modules.watch and modules.cancel (r34, r36).
configure({ asyncUtilTimeout: 5000 });

// The graph canvas draws to a 2D context jsdom does not have: every test gets a stub that renders the
// nodes and links as buttons and wires the same callbacks.
vi.mock('react-force-graph-2d', async () => import('./forceGraphStub'));
