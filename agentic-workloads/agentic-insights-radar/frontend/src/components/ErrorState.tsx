// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import BreadcrumbGroup from '@cloudscape-design/components/breadcrumb-group';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Spinner from '@cloudscape-design/components/spinner';
import type React from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { can, useSession } from '@/auth/session';
import { NOT_FOR_ROLE } from '@/lib/format';
import { usePageTitle } from '@/lib/pageTitle';

interface Props {
  /** The page title, e.g. "Radar not found". */
  title: string;
  children: React.ReactNode;
  /** Where "Back" goes, and its label. */
  back?: { href: string; label: string };
  onRetry?: () => void;
}

/** A whole-page problem: what happened, a way back, and Retry when trying again can help. */
export function ErrorState({ title, children, back = { href: '/', label: 'Go to Overview' }, onRetry }: Props) {
  const navigate = useNavigate();
  // The tab kept the previous page's title, and nothing was announced (r3 F3-046). A page that names
  // itself (a radar that failed to load) still does so: its own title effect runs after this one.
  usePageTitle(title);
  return (
    <ContentLayout header={<Header variant="h1">{title}</Header>}>
      <Container>
        <SpaceBetween size="m">
          <Box>{children}</Box>
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant={onRetry ? 'normal' : 'primary'} onClick={() => navigate(back.href)}>{back.label}</Button>
            {onRetry && <Button variant="primary" onClick={onRetry}>Retry</Button>}
          </SpaceBetween>
        </SpaceBetween>
      </Container>
    </ContentLayout>
  );
}

/**
 * A failed page load: "not found" for a 404 (nothing to retry), otherwise the error with Retry.
 * `forRole` is for a viewer, who gets a 404 for a record kept from their role: 'hidden' when it
 * certainly is one (a gap, a content idea, a draft), 'maybe' when it could also have been deleted.
 */
export function LoadError({
  error,
  what,
  back,
  onRetry,
  forRole,
}: {
  error: Error;
  what: string;
  back: { href: string; label: string };
  onRetry: () => void;
  forRole?: 'hidden' | 'maybe';
}) {
  if (error instanceof ApiError && error.status === 404 && forRole === 'hidden') {
    return (
      <ErrorState title="Not available to your role" back={back}>
        {NOT_FOR_ROLE}
      </ErrorState>
    );
  }
  if (error instanceof ApiError && error.status === 404 && forRole === 'maybe') {
    return (
      <ErrorState title={`${what} not available`} back={back}>
        It is not available to your role, or it has been deleted. Some records are kept from viewers because they come from private
        product documents; ask an administrator if you need it.
      </ErrorState>
    );
  }
  if (error instanceof ApiError && error.status === 404) {
    return (
      <ErrorState title={`${what} not found`} back={back}>
        It may have been deleted, or the address may be mistyped.
      </ErrorState>
    );
  }
  return (
    <ErrorState title={`This ${what.toLowerCase()} could not be loaded`} back={back} onRetry={onRetry}>
      {error.message}
    </ErrorState>
  );
}

/**
 * A background re-read that failed while the page still shows what it last read: said in a banner
 * with Retry, so the page (and any draft on it) stays where it is (r3 F3-002, F3-003).
 */
export function ReloadFailed({ error, what, onRetry }: { error: Error; what: string; onRetry: () => void }) {
  return (
    <Alert type="error" header={`The ${what} could not be read again`} action={<Button onClick={onRetry}>Retry</Button>}>
      {`This page still shows what it read last, and nothing you have not saved is lost. ${error.message}`}
    </Alert>
  );
}

/**
 * A table's `empty` slot after its load failed: a failed load must never read as "there are none",
 * which invites adding the same sources or documents again (r3 F3-018).
 */
export function TableLoadError({ what, error, onRetry }: { what: string; error: Error; onRetry: () => void }) {
  return (
    <Box textAlign="center" padding="m">
      <SpaceBetween size="xs" alignItems="center">
        <StatusIndicator type="error">{`The ${what} could not be loaded: ${error.message}`}</StatusIndicator>
        <Button onClick={onRetry}>Retry</Button>
      </SpaceBetween>
    </Box>
  );
}

/** A centred loading state with a label, used while a page's first request runs. */
export function PageLoading({ label = 'Loading' }: { label?: string }) {
  return (
    <Box textAlign="center" padding="xxl" color="text-body-secondary">
      <Spinner size="large" />
      <Box variant="p" color="inherit">{label}</Box>
    </Box>
  );
}

/** The path back from a detail page ("Radars > Rivals"), followed inside the app. */
export function Crumbs({ items }: { items: { text: string; href: string }[] }) {
  const navigate = useNavigate();
  return (
    <BreadcrumbGroup
      items={items}
      ariaLabel="Breadcrumbs"
      onFollow={(e) => {
        e.preventDefault();
        navigate(e.detail.href);
      }}
    />
  );
}

export function NotFound() {
  return (
    <ErrorState title="Page not found">
      This page does not exist. Check the address, or start again from the Overview.
    </ErrorState>
  );
}

/** A part of the app this deployment turned off in its profile (`sections`), instead of a page that
 * looks broken or empty. */
export function SectionOff({ what }: { what: string }) {
  return (
    <ErrorState title={`${what} is turned off`}>
      {`This deployment does not use ${what}. An administrator can turn it on in the deployment profile (sections).`}
    </ErrorState>
  );
}

/** Renders the page only for people allowed to `action`; others see why, instead of a page of failing requests. */
export function RequireCapability({ action, what, children }: { action: string; what: string; children: React.ReactNode }) {
  const { me, meLoading } = useSession();
  if (!me && meLoading) return <PageLoading />;
  if (!can(me, action)) {
    return (
      <ErrorState title="You need administrator access">
        {`${what} is for administrators. Ask an administrator if you need access.`}
      </ErrorState>
    );
  }
  return <>{children}</>;
}
