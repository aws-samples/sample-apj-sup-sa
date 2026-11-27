// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Link from '@cloudscape-design/components/link';
import { useNavigate } from 'react-router-dom';

import type { Brief } from '@/api/types';
import { isSafeHref } from '@/lib/html';
import { graphHref, nodeHref } from '@/lib/format';

/** Link to an entry inside the app, plus its external source when it has one. */
export default function NodeLink({ node, showSource = true }: { node: Pick<Brief, 'id' | 'title' | 'evidence_url'>; showSource?: boolean }) {
  const navigate = useNavigate();
  const href = nodeHref(node.id);
  return (
    <span>
      <Link
        href={href}
        onFollow={(e) => {
          e.preventDefault();
          navigate(href);
        }}
      >
        {node.title}
      </Link>
      {showSource && node.evidence_url && isSafeHref(node.evidence_url) && (
        <>
          {' '}
          <Link href={node.evidence_url} external fontSize="body-s" variant="secondary">
            source
          </Link>
        </>
      )}
    </span>
  );
}

/** "View in Knowledge Graph": the graph page opened at this node with its neighbours drawn. */
export function GraphLink({ id }: { id: string }) {
  const navigate = useNavigate();
  const href = graphHref(id);
  return (
    <Link
      href={href}
      onFollow={(e) => {
        e.preventDefault();
        navigate(href);
      }}
    >
      View in Knowledge Graph
    </Link>
  );
}
