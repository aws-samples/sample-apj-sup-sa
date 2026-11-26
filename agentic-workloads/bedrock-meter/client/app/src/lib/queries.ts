import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteUserLimit,
  forceUnblockUser,
  getAudit,
  type AuditFilters,
  getBlockedUsers,
  getLimits,
  getModelTrend,
  getOverview,
  getUser,
  getUserHistory,
  getUserLimit,
  getUsers,
  saveUserLimits,
  saveQuotaTimeZone,
  type HistoryGranularity,
  type LimitUpdateInput,
  type ModelTrendFilters,
  type ReasonedUserInput,
  type QuotaTimeZoneUpdateInput,
  type UserFilters,
} from "@/lib/api";

export const queryKeys = {
  overview: ["overview"] as const,
  users: (filters: UserFilters) => ["users", filters] as const,
  user: (username: string) => ["user", username] as const,
  history: (username: string, granularity: HistoryGranularity = "day") =>
    ["history", username, granularity] as const,
  limits: ["limits"] as const,
  limit: (username: string) => ["limit", username] as const,
  blocked: ["blocked"] as const,
  audit: (filters: AuditFilters = {}) => ["audit", filters] as const,
  modelTrend: (filters: ModelTrendFilters) => ["model-trend", filters] as const,
};

export function useOverview() {
  return useQuery({ queryKey: queryKeys.overview, queryFn: getOverview });
}

export function useModelTrend(filters: ModelTrendFilters, enabled = true) {
  return useQuery({
    queryKey: queryKeys.modelTrend(filters),
    queryFn: () => getModelTrend(filters),
    placeholderData: (previous) => previous,
    enabled,
  });
}

export function useUsers(filters: UserFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.users(filters),
    queryFn: ({ pageParam }) => getUsers({ ...filters, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
    placeholderData: (previous) => previous,
  });
}

export function useUser(username: string) {
  return useQuery({ queryKey: queryKeys.user(username), queryFn: () => getUser(username) });
}

export function useUserHistory(username: string, granularity: HistoryGranularity = "day") {
  return useQuery({
    queryKey: queryKeys.history(username, granularity),
    queryFn: () => getUserHistory(username, granularity),
    placeholderData: (previous) => previous,
  });
}

export function useLimits() {
  return useQuery({ queryKey: queryKeys.limits, queryFn: getLimits });
}

export function useUserLimit(username: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.limit(username),
    queryFn: () => getUserLimit(username),
    enabled,
  });
}

export function useBlockedUsers() {
  return useQuery({ queryKey: queryKeys.blocked, queryFn: getBlockedUsers });
}

export function useAudit(filters: AuditFilters = {}) {
  return useInfiniteQuery({
    queryKey: queryKeys.audit(filters),
    queryFn: ({ pageParam }) => getAudit({ ...filters, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
    placeholderData: (previous) => previous,
  });
}

function useInvalidateConsoleData() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.overview }),
      queryClient.invalidateQueries({ queryKey: ["users"] }),
      queryClient.invalidateQueries({ queryKey: queryKeys.blocked }),
      queryClient.invalidateQueries({ queryKey: queryKeys.limits }),
      queryClient.invalidateQueries({ queryKey: ["audit"] }),
    ]);
}

export function useSaveUserLimits() {
  const invalidate = useInvalidateConsoleData();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: LimitUpdateInput) => saveUserLimits(input),
    onSuccess: (_data, input) =>
      Promise.all([
        invalidate(),
        queryClient.invalidateQueries({ queryKey: queryKeys.limit(input.username) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.user(input.username) }),
      ]),
  });
}

export function useDeleteUserLimit() {
  const invalidate = useInvalidateConsoleData();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ReasonedUserInput) => deleteUserLimit(input),
    onSuccess: (_data, input) =>
      Promise.all([
        invalidate(),
        queryClient.invalidateQueries({ queryKey: queryKeys.limit(input.username) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.user(input.username) }),
      ]),
  });
}

export function useSaveQuotaTimeZone() {
  const invalidate = useInvalidateConsoleData();
  return useMutation({
    mutationFn: (input: QuotaTimeZoneUpdateInput) => saveQuotaTimeZone(input),
    onSuccess: () => invalidate(),
  });
}

export function useForceUnblockUser() {
  const invalidate = useInvalidateConsoleData();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ReasonedUserInput) => forceUnblockUser(input),
    onSuccess: (_data, input) =>
      Promise.all([
        invalidate(),
        queryClient.invalidateQueries({ queryKey: queryKeys.user(input.username) }),
      ]),
  });
}
