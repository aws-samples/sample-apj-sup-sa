"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";
import { useState } from "react";
import { ConsoleApiError } from "@/lib/api";
import { ConsoleConfigProvider } from "@/lib/config";
import { AuthProvider } from "@/lib/auth";

export function Providers({ children }: PropsWithChildren) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // No stale caching of values: data is always considered stale, so every
            // mount hits the BFF for live data (prior value may flash as a placeholder
            // while the refetch is in flight, then is replaced by the live result).
            staleTime: 0,
            refetchOnMount: "always",
            refetchOnWindowFocus: false,
            retry: (failureCount, error) => {
              if (error instanceof ConsoleApiError && (error.status < 500 || error.status === 503)) {
                return false;
              }
              return failureCount < 2;
            },
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ConsoleConfigProvider>
        <AuthProvider>{children}</AuthProvider>
      </ConsoleConfigProvider>
    </QueryClientProvider>
  );
}
