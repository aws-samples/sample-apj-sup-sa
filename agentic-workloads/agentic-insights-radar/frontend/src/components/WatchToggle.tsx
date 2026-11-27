// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Button from '@cloudscape-design/components/button';
import React from 'react';

import { useSession } from '@/auth/session';
import { useNotify } from '@/components/Notifications';
import { useAsync } from '@/hooks/useAsync';

/** Watch a graph record: what arrives about it is listed in What's new (CTR subscriptions). */
export default function WatchToggle({ id }: { id: string }) {
  const { api } = useSession();
  const { fail } = useNotify();
  const loaded = useAsync(() => api.watches(), [api]);
  const [local, setLocal] = React.useState<boolean | null>(null);
  const on = local ?? Boolean(loaded.data?.items.some((w) => w.id === id));
  const toggle = async () => {
    setLocal(!on); // shown at once; put back if the server refuses
    try {
      await api.setWatch(id, !on);
    } catch (err) {
      setLocal(on);
      fail(err);
    }
  };
  return (
    <Button iconName={on ? 'notification' : 'add-plus'} disabled={!loaded.data} onClick={() => void toggle()}>
      {on ? 'Watching' : 'Watch'}
    </Button>
  );
}
