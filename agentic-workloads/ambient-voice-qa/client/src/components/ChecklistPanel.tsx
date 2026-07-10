import { useState } from 'react';

import { RTVIEvent } from '@pipecat-ai/client-js';
import { useRTVIClientEvent } from '@pipecat-ai/client-react';

type ChecklistItem = {
  id: string;
  step: number;
  title: string;
  prompt?: string;
  unit?: string;
  type: 'numeric' | 'boolean';
  min?: number;
  max?: number;
};

type ResultItem = {
  id: string;
  step: number;
  title: string;
  status: 'pending' | 'pass' | 'anomaly' | 'skipped';
  value: string | number | null;
  anomaly: boolean;
  note: string | null;
};

type Snapshot = {
  items: ResultItem[];
  current_index: number;
  current_id: string | null;
  complete: boolean;
  anomalies: ResultItem[];
};

type ServerEnvelope =
  | { type: 'checklist_init'; payload: { checklist: ChecklistItem[] } }
  | { type: 'checklist_update'; payload: Snapshot };

type IncomingMessage =
  | ServerEnvelope
  | { data: ServerEnvelope }
  | unknown;

const statusColor: Record<ResultItem['status'], string> = {
  pending: 'border-gray-400 text-gray-400',
  pass: 'border-emerald-500 text-emerald-400',
  anomaly: 'border-red-500 text-red-400',
  skipped: 'border-yellow-500 text-yellow-400',
};

const statusBadge: Record<ResultItem['status'], string> = {
  pending: 'PENDING',
  pass: 'PASS',
  anomaly: 'ANOMALY',
  skipped: 'SKIPPED',
};

export const ChecklistPanel = () => {
  const [checklist, setChecklist] = useState<ChecklistItem[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);

  // The react package re-exports its own RTVIEvent enum; the string value matches.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  useRTVIClientEvent(RTVIEvent.ServerMessage as any, (raw: IncomingMessage) => {
    // Pipecat wraps the payload as {data: {...}} on some transports.
    const env =
      (raw as { data?: ServerEnvelope })?.data ?? (raw as ServerEnvelope);
    if (!env || typeof env !== 'object' || !('type' in env)) return;
    if (env.type === 'checklist_init') {
      setChecklist(env.payload.checklist);
    } else if (env.type === 'checklist_update') {
      setSnapshot(env.payload);
    }
  });

  const items: ResultItem[] =
    snapshot?.items ??
    checklist.map((c) => ({
      id: c.id,
      step: c.step,
      title: c.title,
      status: 'pending' as const,
      value: null,
      anomaly: false,
      note: null,
    }));

  const completed = items.filter(
    (i) => i.status === 'pass' || i.status === 'anomaly' || i.status === 'skipped',
  ).length;
  const anomalyCount = items.filter((i) => i.anomaly).length;
  const total = items.length || checklist.length || 0;
  const pct = total ? Math.round((completed / total) * 100) : 0;

  return (
    <div className="flex flex-col h-full overflow-hidden border border-gray-700 rounded-md bg-black/40">
      <div className="px-4 py-3 border-b border-gray-700">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-xs uppercase tracking-widest text-gray-500">
              Inspection
            </div>
            <div className="text-lg font-semibold text-emerald-400">
              Hydraulic Pump Assembly QA
            </div>
          </div>
          <div className="text-right">
            <div className="text-xs text-gray-500">Progress</div>
            <div className="text-lg font-mono text-emerald-400">
              {completed}/{total} ({pct}%)
            </div>
            {anomalyCount > 0 && (
              <div className="text-xs font-bold text-red-400">
                {anomalyCount} anomaly flagged
              </div>
            )}
          </div>
        </div>
        <div className="w-full h-1 mt-2 bg-gray-800 rounded">
          <div
            className="h-1 bg-emerald-500 rounded transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {items.map((item, idx) => {
          const meta = checklist.find((c) => c.id === item.id);
          const isCurrent = snapshot?.current_index === idx && !snapshot?.complete;
          return (
            <div
              key={item.id}
              className={`p-3 border-l-4 rounded ${statusColor[item.status]} ${
                isCurrent ? 'bg-emerald-900/30 border-emerald-400' : 'bg-gray-900/40'
              }`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <div className="text-sm font-mono text-gray-400">
                  Step {item.step} • {item.id}
                </div>
                <div className="text-xs font-bold">
                  {statusBadge[item.status]}
                  {isCurrent && (
                    <span className="ml-2 text-emerald-400 animate-pulse">
                      ● LIVE
                    </span>
                  )}
                </div>
              </div>
              <div className="text-sm font-semibold text-gray-100 mt-1">
                {item.title}
              </div>
              {meta?.type === 'numeric' && meta.min !== undefined && (
                <div className="text-xs text-gray-500 mt-1">
                  Spec: {meta.min} – {meta.max} {meta.unit}
                </div>
              )}
              {item.value !== null && item.value !== undefined && (
                <div className="text-sm mt-1">
                  Reading:{' '}
                  <span className="font-mono font-bold">{String(item.value)}</span>
                  {meta?.unit ? ` ${meta.unit}` : ''}
                </div>
              )}
              {item.note && (
                <div className="text-xs text-gray-400 mt-1 italic">{item.note}</div>
              )}
            </div>
          );
        })}
        {snapshot?.complete && (
          <div className="p-3 border border-emerald-500 rounded bg-emerald-900/30 text-emerald-300 text-sm font-semibold">
            Inspection complete. {anomalyCount} anomaly{anomalyCount === 1 ? '' : 'ies'} flagged for review.
          </div>
        )}
      </div>
    </div>
  );
};
