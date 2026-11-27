// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Button from '@cloudscape-design/components/button';
import React from 'react';

import { useSession } from '@/auth/session';
import { useNotify } from '@/components/Notifications';
import { useAsync } from '@/hooks/useAsync';

/** The caller's favourite radars, and a way to star or unstar one (CTR sec. 9.3). */
export function useFavourites() {
  const { api } = useSession();
  const { fail } = useNotify();
  const loaded = useAsync(() => api.favourites(), [api]);
  const [local, setLocal] = React.useState<Set<string> | null>(null);
  const set = local ?? new Set(loaded.data?.radars ?? []);
  const toggle = async (slug: string) => {
    const on = !set.has(slug);
    const next = new Set(set);
    if (on) next.add(slug);
    else next.delete(slug);
    setLocal(next); // shown at once; put back if the server refuses
    try {
      await api.setFavourite(slug, on);
    } catch (err) {
      setLocal(set);
      fail(err);
    }
  };
  return { favourites: set, loaded: Boolean(loaded.data), toggle };
}

/** A star on a radar: favourites come first in the radar list and can be shown on their own. */
export default function FavouriteToggle({ slug, name, favourites }: { slug: string; name: string; favourites: ReturnType<typeof useFavourites> }) {
  const on = favourites.favourites.has(slug);
  return (
    <Button
      variant="icon"
      iconName={on ? 'star-filled' : 'star'}
      ariaLabel={on ? `Remove ${name} from favourites` : `Add ${name} to favourites`}
      disabled={!favourites.loaded}
      onClick={() => void favourites.toggle(slug)}
    />
  );
}
