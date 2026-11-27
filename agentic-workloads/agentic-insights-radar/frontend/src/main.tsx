// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import '@cloudscape-design/global-styles/index.css';
import './styles.css';

import Alert from '@cloudscape-design/components/alert';
import Button from '@cloudscape-design/components/button';
import React from 'react';
import { createRoot } from 'react-dom/client';

import type { AppConfig } from '@/api/types';
import { SessionProvider } from '@/auth/session';
import { applyProfile } from '@/lib/profile';

import App from './App';

async function start() {
  const root = createRoot(document.getElementById('root') as HTMLElement);
  try {
    const res = await fetch('/api/config', { headers: { accept: 'application/json' } });
    if (!res.ok) throw new Error(`configuration request failed (${res.status})`);
    const config = (await res.json()) as AppConfig;
    applyProfile(config.profile);
    root.render(
      <React.StrictMode>
        <SessionProvider config={config}>
          <App />
        </SessionProvider>
      </React.StrictMode>,
    );
  } catch (err) {
    root.render(
      <div className="air-signin">
        <Alert type="error" header="Agentic Insights Radar could not start" action={<Button onClick={() => window.location.reload()}>Retry</Button>}>
          The app could not load its settings. This is usually brief: try again in a moment. ({String(err)})
        </Alert>
      </div>,
    );
  }
}

void start();
