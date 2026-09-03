import type { ChangeEvent, DragEvent, ReactNode } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { LogLevel, RTVIEvent } from '@pipecat-ai/client-js';
import type { PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import {
  ConnectButton,
  EventsPanel,
  UserAudioControl,
  usePipecatConversation,
} from '@pipecat-ai/voice-ui-kit';
import type { BotOutputText, ConversationMessage } from '@pipecat-ai/voice-ui-kit';

import { TRANSPORT_LABELS, type TransportType } from '../config';
import { VoiceAgentClient } from '../voiceAgentClient';
import type { VoiceAgentLatencyEvent, VoiceAgentStatus } from '../voiceAgentClient';
import { TransportSelect } from './TransportSelect';

type AppScreen = 'start' | 'details' | 'people' | 'evidence' | 'review' | 'submitted';
type AppPhase = 'dialer' | 'connecting' | 'call' | 'secureLink' | 'verified' | 'app' | 'claimSms';
type VoiceRuntime = 'pipecat' | 'voice_agent';

type ClaimFieldKey =
  | 'incidentType'
  | 'incidentTime'
  | 'location'
  | 'vehicle'
  | 'damageArea'
  | 'injuryStatus'
  | 'otherPartyStatus'
  | 'policeReportStatus';

type EvidenceStatus = 'waiting' | 'uploading' | 'attached' | 'analyzing' | 'analyzed' | 'rejected';
type FieldTone = 'success' | 'warn' | 'neutral';
type ToolStatus = 'queued' | 'running' | 'complete' | 'failed';
type RiskLevel = 'Low' | 'Medium' | 'High';
type SupervisorStepStatus = 'Complete' | 'Active' | 'Missing' | 'Pending' | 'Blocked';

interface TimelineEntry {
  id: string;
  time: string;
  description: string;
  source: string;
  status?: string;
  updated?: boolean;
}

interface FieldState {
  value: string;
  status: string;
  tone: FieldTone;
  missing?: boolean;
  updated?: boolean;
  note?: string;
}

interface ClaimState {
  claimId: string;
  status: 'New' | 'Draft' | 'Submitted';
  claimType: string;
  policyId: string;
  whatHappenedSummary: string;
  safetySummary: string;
  fields: Record<ClaimFieldKey, FieldState>;
  requestedEvidence: string;
  evidenceStatus: EvidenceStatus;
  evidenceResult: string;
  evidenceSeverity: string;
  evidenceAnalysisProvider: string;
  evidenceNotes: string[];
  evidenceId: string;
  evidenceUploadUrl: string;
  evidenceS3Key: string;
  evidenceStorage: string;
  selectedImageUrl: string;
  evidenceFileName: string;
  timeline: TimelineEntry[];
  preSubmitSummary: string;
  missingInformation: string[];
  customerSummary: string;
  adjusterSummary: string;
  customerFollowUp: string;
  nextActions: string[];
  finalPacketStorage: string;
  faultDecision: {
    available: boolean;
    faultDecision?: string;
    excessEstimate?: string;
    explanation?: string;
    reason?: string;
  };
}

interface EventLogEntry {
  id: string;
  time: string;
  title: string;
  detail: string;
}

interface TranscriptEntry {
  id: string;
  speaker: 'Customer' | 'Assistant' | 'System';
  text: string;
}

interface SupervisorWorkflowItem {
  step: string;
  status: SupervisorStepStatus;
  detail: string;
}

interface SupervisorState {
  workflow: SupervisorWorkflowItem[];
  currentStep: string;
  blockers: string[];
  triageRiskPercent: number;
  triageRiskLevel: RiskLevel;
  triageSignals: string[];
  previousClaimHistory: string;
  recommendation: string;
}

interface ToolEventEntry {
  id: string;
  time: string;
  tool: string;
  status: ToolStatus;
  detail: string;
  payloadPreview?: string;
}

interface AwsProofEntry {
  id: string;
  time: string;
  service: string;
  detail: string;
  label?: string;
}

interface UiAction {
  type?: string;
  action: string;
  screen?: string;
  field?: string;
  fields?: Array<{
    field?: string;
    value?: string;
    status?: string;
    note?: string;
  }>;
  value?: string;
  note?: string;
  status?: string;
  claimId?: string;
  severity?: string;
  requestedEvidence?: string;
  incidentSummary?: string;
  safetySummary?: string;
  summary?: string;
  customerSummary?: string;
  adjusterSummary?: string;
  recommendedNextAction?: string;
  customerFollowUp?: string;
  evidenceId?: string;
  uploadUrl?: string;
  phoneNumber?: string;
  secureLink?: string;
  s3Key?: string;
  storage?: string;
  contentType?: string;
  analysisProvider?: string;
  notes?: string[];
  events?: TimelineEntry[];
  timeline?: TimelineEntry[];
  supervisor?: Partial<SupervisorState>;
  tool?: string;
  detail?: string;
  payloadPreview?: string;
  service?: string;
  label?: string;
  faultDecision?: string;
  excessEstimate?: string;
  explanation?: string;
  reason?: string;
  payload?: Record<string, unknown>;
}

interface EvidenceRequestOptions {
  evidenceId?: string;
  uploadUrl?: string;
  s3Key?: string;
  storage?: string;
  contentType?: string;
  analysisProvider?: string;
  notes?: string[];
  evidenceMismatch?: boolean;
}

interface PendingEvidenceUpload {
  file: File;
  fileName: string;
  imageUrl: string;
  requestedEvidence: string;
}

type PipecatConsoleClient = {
  on: (event: string, handler: (...args: unknown[]) => void) => void;
  off: (event: string, handler: (...args: unknown[]) => void) => void;
  setLogLevel: (level: LogLevel) => void;
};

interface AppProps extends PipecatBaseChildProps {
  transportType: TransportType;
  onTransportChange: (type: TransportType) => void;
  availableTransports: TransportType[];
}

declare global {
  interface Window {
    ClaimPilot?: {
      dispatch: (action: UiAction) => void;
      runDemo: () => void;
      reset: () => void;
      getState: () => { screen: AppScreen; claim: ClaimState };
    };
  }
}

const FIELD_LABELS: Record<ClaimFieldKey, string> = {
  incidentType: 'Incident type',
  incidentTime: 'Time of incident',
  location: 'Location',
  vehicle: 'Vehicle',
  damageArea: 'Damage area',
  injuryStatus: 'Injury status',
  otherPartyStatus: 'Other party status',
  policeReportStatus: 'Police report',
};

const FIELD_ORDER: ClaimFieldKey[] = [
  'incidentType',
  'incidentTime',
  'location',
  'vehicle',
  'damageArea',
  'injuryStatus',
  'otherPartyStatus',
  'policeReportStatus',
];

const CLAIMS_PHONE_NUMBER = '132 480';
const SECURE_CLAIM_LINK_BASE = 'northstar.app.link/claim';
const PHONE_CONNECTION_DELAY_MS = Number(
  import.meta.env.VITE_CLAIMPILOT_PHONE_CONNECTION_DELAY_MS ?? 3500
);
const SECURE_LINK_DELIVERY_DELAY_MS = Number(
  import.meta.env.VITE_CLAIMPILOT_SECURE_LINK_DELAY_MS ?? 10000
);
const CLAIMPILOT_PHONE_PROMPT =
  'Hi, welcome to Northstar Insurance. I can help you start your car insurance claim. Would you like to process this claim over the phone and continue securely in the Northstar app?';

const EVIDENCE_STATUS_LABELS: Record<EvidenceStatus, string> = {
  waiting: 'Waiting',
  uploading: 'Uploading',
  attached: 'Attached',
  analyzing: 'Analyzing',
  analyzed: 'Analyzed',
  rejected: 'Needs new photo',
};

const STEP_BY_SCREEN: Record<AppScreen, string> = {
  start: 'start',
  details: 'details',
  people: 'details',
  evidence: 'evidence',
  review: 'submit',
  submitted: 'submit',
};

const STEPS = ['start', 'details', 'evidence', 'submit'];

const NAV_ITEMS: { screen: AppScreen; label: string; icon: keyof typeof iconPaths }[] = [
  { screen: 'start', label: 'Home', icon: 'home' },
  { screen: 'details', label: 'Claim', icon: 'file' },
  { screen: 'evidence', label: 'Evidence', icon: 'camera' },
  { screen: 'review', label: 'Review', icon: 'summary' },
];

const EMPTY_FIELDS: Record<ClaimFieldKey, FieldState> = {
  incidentType: { value: '', status: 'Not started', tone: 'neutral', missing: true },
  incidentTime: { value: '', status: 'Needed', tone: 'warn', missing: true },
  location: { value: '', status: 'Needed', tone: 'warn', missing: true },
  vehicle: { value: '', status: 'Needed', tone: 'warn', missing: true },
  damageArea: { value: '', status: 'Needed', tone: 'warn', missing: true },
  injuryStatus: { value: '', status: 'Needed', tone: 'warn', missing: true },
  otherPartyStatus: { value: '', status: 'Needed', tone: 'warn', missing: true },
  policeReportStatus: {
    value: '',
    status: 'Needed',
    tone: 'warn',
    missing: true,
    note: 'The assistant will ask for this if available',
  },
};

const START_EVENTS: EventLogEntry[] = [
  {
    id: 'event-ready',
    time: '09:41',
    title: 'Claim assistant ready',
    detail: 'Waiting to start a new claim',
  },
];

const START_TRANSCRIPT: TranscriptEntry[] = [
  {
    id: 'transcript-ready',
    speaker: 'Assistant',
    text: 'Waiting for the customer to dial the Northstar claims number.',
  },
];

const START_TOOL_EVENTS: ToolEventEntry[] = [
  {
    id: 'tool-waiting',
    time: '00:00',
    tool: 'waiting_for_call',
    status: 'queued',
    detail: 'Connect Pipecat voice or use the demo event simulator.',
  },
];

const SUPERVISOR_WORKFLOW: SupervisorWorkflowItem[] = [
  { step: 'Collect incident fields', status: 'Pending', detail: 'Waiting for voice intake' },
  { step: 'Confirm safety and injuries', status: 'Pending', detail: 'Safety statement not captured' },
  { step: 'Build timeline', status: 'Pending', detail: 'Timeline will be extracted from the call' },
  { step: 'Collect photos', status: 'Pending', detail: 'Evidence not requested yet' },
  { step: 'Collect police report', status: 'Pending', detail: 'Can be added after intake' },
  { step: 'Review claim summary', status: 'Pending', detail: 'Pre-submit packet not generated' },
  { step: 'Submit claim', status: 'Pending', detail: 'Waiting for customer review' },
];

const SAMPLE_TIMELINE: TimelineEntry[] = [
  {
    id: 'timeline-reported',
    time: 'Now',
    description: 'Customer reported a rear-end collision during the call.',
    source: 'Deepgram live voice',
    status: 'Captured',
  },
  {
    id: 'timeline-incident',
    time: '20 minutes ago',
    description: 'Vehicle was rear-ended near Norton Street.',
    source: 'Voice intake',
    status: 'Captured',
  },
  {
    id: 'timeline-safety',
    time: 'After incident',
    description: 'Customer confirmed they are shaken up but do not think anyone is hurt.',
    source: 'ClaimPilot safety check',
    status: 'Captured',
  },
];

const iconPaths: Record<string, ReactNode> = {
  shield: (
    <>
      <path d="M12 3 5 6v5c0 4.6 2.8 8.2 7 10 4.2-1.8 7-5.4 7-10V6l-7-3Z" />
      <path d="m9 12 2 2 4-5" />
    </>
  ),
  car: (
    <>
      <path d="M6 17h12l1-5-2-4H7l-2 4 1 5Z" />
      <path d="M7 17v2M17 17v2M8 12h8" />
    </>
  ),
  glass: (
    <>
      <path d="M4 7h16v10H4z" />
      <path d="m8 7 8 10M13 7l4 10" />
    </>
  ),
  chat: (
    <>
      <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4v8Z" />
      <path d="M8 9h8M8 13h5" />
    </>
  ),
  home: (
    <>
      <path d="M3 11 12 4l9 7" />
      <path d="M5 10v10h14V10" />
    </>
  ),
  file: (
    <>
      <path d="M7 3h10l2 3v15H5V6l2-3Z" />
      <path d="M8 9h8M8 13h8M8 17h5" />
    </>
  ),
  camera: (
    <>
      <path d="M4 8h4l2-3h4l2 3h4v11H4V8Z" />
      <circle cx="12" cy="13" r="3" />
    </>
  ),
  summary: (
    <>
      <path d="M9 11h6M9 15h6" />
      <path d="M7 3h7l3 3v15H7V3Z" />
      <path d="M14 3v4h4" />
    </>
  ),
  phone: (
    <>
      <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.4 19.4 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.7.6 2.5a2 2 0 0 1-.5 2.1L8 9.5a16 16 0 0 0 6.5 6.5l1.2-1.2a2 2 0 0 1 2.1-.5c.8.3 1.6.5 2.5.6a2 2 0 0 1 1.7 2Z" />
    </>
  ),
  mic: (
    <>
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3" />
    </>
  ),
  list: (
    <>
      <path d="M8 6h13M8 12h13M8 18h13" />
      <path d="M3 6h.01M3 12h.01M3 18h.01" />
    </>
  ),
  database: (
    <>
      <path d="M21 5c0 1.7-4 3-9 3S3 6.7 3 5s4-3 9-3 9 1.3 9 3Z" />
      <path d="M3 5v6c0 1.7 4 3 9 3s9-1.3 9-3V5M3 11v6c0 1.7 4 3 9 3s9-1.3 9-3v-6" />
    </>
  ),
  aws: (
    <>
      <path d="M6 15c-2.2 0-4-1.5-4-3.5S3.8 8 6 8c.7-2.3 2.8-4 5.3-4 3.1 0 5.7 2.4 5.9 5.5H18a4 4 0 0 1 0 8H6Z" />
    </>
  ),
  check: <path d="m20 6-11 11-5-5" />,
  alert: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v5M12 16h.01" />
    </>
  ),
  upload: (
    <>
      <path d="M12 16V4" />
      <path d="m7 9 5-5 5 5" />
      <path d="M5 20h14" />
    </>
  ),
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function formatTime() {
  return new Intl.DateTimeFormat('en-AU', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date());
}

function makeId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.round(Math.random() * 100000)}`;
}

function formatElapsed() {
  const elapsedSeconds = Math.max(0, Math.round((Date.now() - performance.timeOrigin) / 1000) % 600);
  const minutes = Math.floor(elapsedSeconds / 60).toString().padStart(2, '0');
  const seconds = (elapsedSeconds % 60).toString().padStart(2, '0');
  return `${minutes}:${seconds}`;
}

function getActionValue(action: UiAction, key: string): unknown {
  const direct = (action as unknown as Record<string, unknown>)[key];
  if (direct !== undefined) return direct;
  return action.payload?.[key];
}

function getPayloadString(action: UiAction, key: string) {
  const value = getActionValue(action, key);
  return typeof value === 'string' ? value : undefined;
}

function getPayloadStrings(action: UiAction, key: string) {
  const value = getActionValue(action, key);
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : undefined;
}

function getPayloadNumber(action: UiAction, key: string) {
  const value = getActionValue(action, key);
  return typeof value === 'number' ? value : undefined;
}

function getPayloadObject<T extends object>(action: UiAction, key: string) {
  const value = getActionValue(action, key);
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Partial<T>) : undefined;
}

function getTimelinePayload(action: UiAction) {
  const events = getActionValue(action, 'events') ?? getActionValue(action, 'timeline');
  if (!Array.isArray(events)) return undefined;

  return events
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    .map((item, index) => ({
      id: typeof item.id === 'string' ? item.id : makeId(`timeline-${index}`),
      time: typeof item.time === 'string' ? item.time : 'Now',
      description: typeof item.description === 'string' ? item.description : '',
      source: typeof item.source === 'string' ? item.source : 'Pipecat tool call',
      status: typeof item.status === 'string' ? item.status : 'Captured',
      updated: Boolean(item.updated),
    }))
    .filter((item) => item.description);
}

function normalizeToolStatus(value?: string): ToolStatus {
  if (value === 'queued' || value === 'running' || value === 'complete' || value === 'failed') {
    return value;
  }
  if (value === 'Complete') return 'complete';
  if (value === 'Running') return 'running';
  if (value === 'Failed') return 'failed';
  return 'complete';
}

function normalizeRiskLevel(value?: string): RiskLevel {
  if (value === 'High' || value === 'Medium' || value === 'Low') return value;
  return 'Medium';
}

function emptySupervisor(): SupervisorState {
  return {
    workflow: clone(SUPERVISOR_WORKFLOW),
    currentStep: 'Waiting for customer call',
    blockers: ['Claim intake has not started'],
    triageRiskPercent: 42,
    triageRiskLevel: 'Medium',
    triageSignals: [
      'No incident timeline captured yet',
      'Evidence not attached',
      'Previous claim history not checked',
    ],
    previousClaimHistory: 'Not checked yet',
    recommendation: 'Start the Pipecat guided intake or run the local event demo.',
  };
}

function emptyClaim(): ClaimState {
  return {
    claimId: 'Draft',
    status: 'New',
    claimType: '',
    policyId: 'AU-4829',
    whatHappenedSummary: '',
    safetySummary: '',
    fields: clone(EMPTY_FIELDS),
    requestedEvidence: '',
    evidenceStatus: 'waiting',
    evidenceResult: '',
    evidenceSeverity: 'Pending',
    evidenceAnalysisProvider: '',
    evidenceNotes: [],
    evidenceId: '',
    evidenceUploadUrl: '',
    evidenceS3Key: '',
    evidenceStorage: '',
    selectedImageUrl: '',
    evidenceFileName: '',
    timeline: [],
    preSubmitSummary: '',
    missingInformation: ['Police report number', "Other driver's license plate"],
    customerSummary: 'A new auto claim has not been started yet.',
    adjusterSummary: 'Claim packet will be generated after the assistant captures the claim details.',
    customerFollowUp: '',
    nextActions: ['Start auto claim intake'],
    finalPacketStorage: '',
    faultDecision: {
      available: false,
      reason: 'Fault and excess estimate will be available after supervisor review.',
    },
  };
}

function displayValue(field: FieldState, fallback = 'Not captured yet') {
  return field.value || fallback;
}

function resolveScreen(screen?: string): AppScreen | undefined {
  if (!screen) return undefined;

  const aliases: Record<string, AppScreen> = {
    home: 'start',
    start: 'start',
    StartClaim: 'start',
    IncidentContext: 'details',
    incident: 'details',
    ClaimDetails: 'details',
    details: 'details',
    claim: 'details',
    PeopleDetails: 'people',
    people: 'people',
    EvidenceUpload: 'evidence',
    evidence: 'evidence',
    EvidenceAnalysis: 'review',
    ReviewPacket: 'review',
    review: 'review',
    FinalClaimPacket: 'submitted',
    ClaimPacket: 'submitted',
    summary: 'submitted',
    submitted: 'submitted',
  };

  return aliases[screen];
}

function resolveField(field?: string): ClaimFieldKey | undefined {
  if (!field) return undefined;

  const aliases: Record<string, ClaimFieldKey> = {
    incidentType: 'incidentType',
    incidentTime: 'incidentTime',
    time: 'incidentTime',
    location: 'location',
    vehicle: 'vehicle',
    vehicleOwner: 'vehicle',
    damageArea: 'damageArea',
    areaDamaged: 'damageArea',
    injuryStatus: 'injuryStatus',
    otherPartyStatus: 'otherPartyStatus',
    policeReportStatus: 'policeReportStatus',
    policeReportNumber: 'policeReportStatus',
  };

  return aliases[field];
}

function getPayloadFieldUpdates(action: UiAction) {
  const value = getActionValue(action, 'fields');
  if (!Array.isArray(value)) return undefined;

  return value
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    .map((item) => ({
      field: typeof item.field === 'string' ? item.field : undefined,
      value: typeof item.value === 'string' ? item.value : undefined,
      status: typeof item.status === 'string' ? item.status : undefined,
      note: typeof item.note === 'string' ? item.note : undefined,
    }));
}

function getEvidenceProgress(status: EvidenceStatus) {
  const progress: Record<EvidenceStatus, number> = {
    waiting: 8,
    uploading: 44,
    attached: 68,
    analyzing: 86,
    analyzed: 100,
    rejected: 100,
  };

  return progress[status];
}

function evidenceStatusTone(status: EvidenceStatus) {
  if (status === 'waiting') return 'warn';
  if (status === 'rejected') return 'danger';
  return 'success';
}

function isEvidenceMismatchResult(value: string, severity: string, notes: string[] = []) {
  const combined = `${value} ${severity} ${notes.join(' ')}`.toLowerCase();
  return (
    combined.includes('wrong photo') ||
    combined.includes('unrelated') ||
    combined.includes('does not show') ||
    combined.includes('doesn\'t show') ||
    combined.includes('not show') ||
    combined.includes('no visible vehicle') ||
    combined.includes('no vehicle damage') ||
    combined.includes('not the requested') ||
    combined.includes('needs new photo') ||
    severity.toLowerCase().includes('reject')
  );
}

function looksLikeUnrelatedEvidence(fileName: string) {
  return /\b(unrelated|wrong|receipt|document|invoice|portrait|profile|selfie|license|food|pet|demo-mismatch)\b/i.test(fileName);
}

function consolePayload(args: unknown[]) {
  if (args.length === 0) return '';
  if (args.length === 1) return args[0];
  return args;
}

function isBotOutputText(value: unknown): value is BotOutputText {
  return Boolean(
    value &&
      typeof value === 'object' &&
      ('spoken' in value || 'unspoken' in value)
  );
}

function conversationPartText(partText: unknown): string {
  if (typeof partText === 'string' || typeof partText === 'number') {
    return String(partText);
  }

  if (isBotOutputText(partText)) {
    return `${partText.spoken ?? ''}${partText.unspoken ?? ''}`;
  }

  return '';
}

function conversationMessageText(message: ConversationMessage): string {
  return message.parts
    .map((part) => conversationPartText(part.text))
    .join('')
    .replace(/\s+/g, ' ')
    .trim();
}

function secureClaimLink(claimId: string) {
  return `${SECURE_CLAIM_LINK_BASE}/${claimId || 'draft'}`;
}

function extractClaimIdFromLink(link?: string) {
  return link?.match(/CLM-\d+/i)?.[0].toUpperCase();
}

function createPipecatConsoleHandler(eventName: string) {
  return (...args: unknown[]) => {
    if (eventName === 'localAudioLevel' || eventName === 'remoteAudioLevel') return;

    const payload = consolePayload(args);

    if (
      eventName === RTVIEvent.Error ||
      eventName === RTVIEvent.MessageError ||
      eventName === RTVIEvent.DeviceError ||
      eventName === RTVIEvent.ScreenShareError
    ) {
      console.error(`[Pipecat:${eventName}]`, payload);
      return;
    }

    console.debug(`[Pipecat:${eventName}]`, payload);
  };
}

function Icon({ name }: { name: keyof typeof iconPaths }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      {iconPaths[name]}
    </svg>
  );
}

function TimelineList({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="timeline-list">
        <div className="timeline-row">
          <strong>Pending</strong>
          <p>Timeline rows will appear when Pipecat extracts events from the call.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="timeline-list">
      {entries.map((entry) => (
        <div className={`timeline-row ${entry.updated ? 'updated' : ''}`} key={entry.id}>
          <strong>{entry.time}</strong>
          <p>
            {entry.description}
            <span>{entry.source}{entry.status ? ` · ${entry.status}` : ''}</span>
          </p>
        </div>
      ))}
    </div>
  );
}

function PipecatTranscriptList() {
  const { messages } = usePipecatConversation({
    botOutputFilter: { spoken: true, unspoken: true },
  });

  const transcriptLines = messages
    .filter((message) => message.role === 'user' || message.role === 'assistant' || message.role === 'system')
    .map((message) => ({
      key: `${message.role}-${message.createdAt}-${message.updatedAt ?? ''}`,
      speaker:
        message.role === 'assistant'
          ? 'Assistant'
          : message.role === 'user'
            ? 'Customer'
            : 'System',
      text: conversationMessageText(message),
      final: message.final ?? message.parts.every((part) => part.final),
    }))
    .filter((line) => line.text)
    .slice(-8);

  if (transcriptLines.length === 0) {
    return (
      <p>
        <strong>Assistant:</strong> Waiting for the customer to dial the Northstar claims number.
      </p>
    );
  }

  return (
    <>
      {transcriptLines.map((line) => (
        <p className={line.final ? '' : 'streaming'} key={line.key}>
          <strong>{line.speaker}:</strong> {line.text}
        </p>
      ))}
    </>
  );
}

function statusTone(status: string) {
  const normalized = status.toLowerCase();
  if (normalized.includes('complete') || normalized.includes('done')) return 'success';
  if (normalized.includes('missing') || normalized.includes('blocked')) return 'warn';
  if (normalized.includes('failed')) return 'danger';
  return '';
}

export const App = ({
  client,
  handleConnect,
  handleDisconnect,
  transportType,
  onTransportChange,
  availableTransports,
}: AppProps) => {
  const [appPhase, setAppPhase] = useState<AppPhase>('dialer');
  const [dialedNumber, setDialedNumber] = useState('');
  const [showSmsNotification, setShowSmsNotification] = useState(false);
  const [claimSmsSent, setClaimSmsSent] = useState(false);
  const [claimSmsText, setClaimSmsText] = useState('');
  const [screen, setScreen] = useState<AppScreen>('start');
  const [claim, setClaim] = useState<ClaimState>(() => emptyClaim());
  const [supervisor, setSupervisor] = useState<SupervisorState>(() => emptySupervisor());
  const [toolEvents, setToolEvents] = useState<ToolEventEntry[]>(START_TOOL_EVENTS);
  const [, setAwsProof] = useState<AwsProofEntry[]>([]);
  const [events, setEvents] = useState<EventLogEntry[]>(START_EVENTS);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>(START_TRANSCRIPT);
  const [toast, setToast] = useState('');
  const [eventBridgeEnabled, setEventBridgeEnabled] = useState(false);
  const [voiceRuntime, setVoiceRuntime] = useState<VoiceRuntime>('pipecat');
  const [voiceAgentStatus, setVoiceAgentStatus] = useState<VoiceAgentStatus>('idle');
  const [voiceAgentDetail, setVoiceAgentDetail] = useState('Deepgram Voice Agent relay not connected');
  const [voiceAgentMuted, setVoiceAgentMuted] = useState(false);
  const [voiceAgentLatency, setVoiceAgentLatency] = useState<VoiceAgentLatencyEvent | null>(null);
  const [freshFields, setFreshFields] = useState<Set<ClaimFieldKey>>(() => new Set());
  const timersRef = useRef<number[]>([]);
  const objectUrlsRef = useRef<string[]>([]);
  const screenViewportRef = useRef<HTMLDivElement>(null);
  const eventSocketRef = useRef<WebSocket | null>(null);
  const voiceAgentClientRef = useRef<VoiceAgentClient | null>(null);
  const pipecatDisconnectRef = useRef(handleDisconnect);
  const evidenceAnalysisRequestRef = useRef(0);
  const pendingEvidenceUploadRef = useRef<PendingEvidenceUpload | null>(null);
  const callConnectionRequestRef = useRef(0);

  useEffect(() => {
    pipecatDisconnectRef.current = handleDisconnect;
  }, [handleDisconnect]);

  const showTransportSelector = availableTransports.length > 1;
  const voiceSourceLabel = voiceRuntime === 'voice_agent' ? 'Deepgram Voice Agent' : 'Pipecat cascade';
  const missingCount = FIELD_ORDER.filter((field) => claim.fields[field].missing).length;
  const completedCount = FIELD_ORDER.length - missingCount;
  const activeStep = STEP_BY_SCREEN[screen];
  const activeStepIndex = STEPS.indexOf(activeStep);
  const uploadProgress = getEvidenceProgress(claim.evidenceStatus);
  const hasEvidencePreview = Boolean(claim.selectedImageUrl || claim.evidenceStatus !== 'waiting');
  const riskTone =
    supervisor.triageRiskLevel === 'Low'
      ? 'success'
      : supervisor.triageRiskLevel === 'High'
        ? 'danger'
        : 'warn';
  const clearTimers = useCallback(() => {
    timersRef.current.forEach((timer) => window.clearTimeout(timer));
    timersRef.current = [];
  }, []);

  const schedule = useCallback((callback: () => void, delay: number) => {
    const timer = window.setTimeout(callback, delay);
    timersRef.current.push(timer);
  }, []);

  const scrollActiveScreenToTop = useCallback(() => {
    window.requestAnimationFrame(() => {
      const activeScreen = screenViewportRef.current?.querySelector('.app-screen.active');
      activeScreen?.scrollTo({ top: 0, left: 0 });
    });
  }, []);

  const navigate = useCallback(
    (nextScreen: AppScreen, message?: string) => {
      setScreen(nextScreen);
      scrollActiveScreenToTop();
      if (message) setToast(message);
    },
    [scrollActiveScreenToTop]
  );

  const addEvent = useCallback((title: string, detail: string) => {
    setEvents((current) => [
      { id: makeId('event'), time: formatTime(), title, detail },
      ...current,
    ].slice(0, 18));
  }, []);

  const connectPipecat = useCallback(async () => {
    try {
      await client?.initDevices();
    } catch (error) {
      console.warn('[Pipecat] Device initialization failed before connect', error);
    }
    await handleConnect?.();
  }, [client, handleConnect]);

  const addTranscript = useCallback((speaker: TranscriptEntry['speaker'], text: string) => {
    const normalizedText = text.trim();
    if (!normalizedText) return;

    setTranscript((current) => {
      const last = current.at(-1);
      if (last?.speaker === speaker && last.text === normalizedText) return current;
      return [
        ...current,
        { id: makeId('transcript'), speaker, text: normalizedText },
      ].slice(-8);
    });
  }, []);

  const appendToolEvent = useCallback(
    (tool: string, status: ToolStatus = 'complete', detail = 'Tool call completed', payloadPreview?: string) => {
      setToolEvents((current) => [
        {
          id: makeId('tool'),
          time: formatElapsed(),
          tool,
          status,
          detail,
          payloadPreview,
        },
        ...current.filter((entry) => entry.id !== 'tool-waiting'),
      ].slice(0, 18));
    },
    []
  );

  const appendAwsProof = useCallback((service: string, detail: string, label?: string) => {
    if (!detail) return;

    setAwsProof((current) => [
      {
        id: makeId('aws'),
        time: formatTime(),
        service,
        detail,
        label,
      },
      ...current.filter((entry) => !(entry.service === service && entry.detail === detail)),
    ].slice(0, 12));
  }, []);

  const setWorkflowStatus = useCallback(
    (step: string, status: SupervisorStepStatus, detail?: string) => {
      setSupervisor((current) => ({
        ...current,
        workflow: current.workflow.map((item) =>
          item.step === step ? { ...item, status, detail: detail ?? item.detail } : item
        ),
      }));
    },
    []
  );

  const mergeSupervisorState = useCallback((next: Partial<SupervisorState>) => {
    setSupervisor((current) => ({
      ...current,
      ...next,
      triageRiskLevel: next.triageRiskLevel ? normalizeRiskLevel(next.triageRiskLevel) : current.triageRiskLevel,
      workflow: next.workflow
        ? next.workflow.map((item) => ({
            step: item.step ?? 'Workflow step',
            status: item.status ?? 'Pending',
            detail: item.detail ?? '',
          }))
        : current.workflow,
      blockers: next.blockers ?? current.blockers,
      triageSignals: next.triageSignals ?? current.triageSignals,
    }));
  }, []);

  const updateTimeline = useCallback(
    (timeline: TimelineEntry[]) => {
      setClaim((current) => ({ ...current, timeline }));
      addEvent('Timeline updated', `${timeline.length} structured events extracted`);
      appendToolEvent('update_timeline', 'complete', `${timeline.length} structured events extracted`);
      setWorkflowStatus('Build timeline', 'Complete', 'Structured from voice intake');
    },
    [addEvent, appendToolEvent, setWorkflowStatus]
  );

  const sendAppEvent = useCallback((action: string, payload: Record<string, unknown> = {}) => {
    if (voiceRuntime === 'voice_agent') {
      const sent = voiceAgentClientRef.current?.sendAppEvent(action, payload) ?? false;
      if (!sent) {
        console.info(`[ClaimPilot] Voice Agent relay unavailable for browser event: ${action}`);
      }
      return sent;
    }

    const socket = eventSocketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      console.info(`[ClaimPilot] UI event stream unavailable for browser event: ${action}`);
      return false;
    }

    socket.send(JSON.stringify({ type: 'app_event', action, payload, ...payload }));
    return true;
  }, [voiceRuntime]);

  const sendPendingEvidenceUploadRequest = useCallback(() => {
    const pendingUpload = pendingEvidenceUploadRef.current;
    if (!pendingUpload) return false;

    const sent = sendAppEvent('manual_evidence_upload_requested', {
      requestedEvidence: pendingUpload.requestedEvidence,
      fileName: pendingUpload.fileName,
      contentType: pendingUpload.file.type || 'image/jpeg',
      fileSize: pendingUpload.file.size,
    });
    if (sent) {
      appendToolEvent('request_evidence', 'running', 'S3 upload target request sent');
    }
    return sent;
  }, [appendToolEvent, sendAppEvent]);

  const flashField = useCallback((field: ClaimFieldKey) => {
    setFreshFields((current) => new Set(current).add(field));
    window.setTimeout(() => {
      setFreshFields((current) => {
        const next = new Set(current);
        next.delete(field);
        return next;
      });
    }, 1600);
  }, []);

  const updateClaimField = useCallback(
    (field: ClaimFieldKey, value: string, options?: Partial<FieldState> & { toast?: string }) => {
      setClaim((current) => ({
        ...current,
        status: current.status === 'New' ? 'Draft' : current.status,
        fields: {
          ...current.fields,
          [field]: {
            value,
            status: options?.status ?? 'Filled',
            tone: options?.tone ?? 'success',
            missing: options?.missing ?? false,
            updated: options?.updated ?? true,
            note: options?.note ?? 'Captured from voice',
          },
        },
      }));
      flashField(field);
      setToast(options?.toast ?? `Assistant updated ${FIELD_LABELS[field].toLowerCase()}`);
      addEvent('Claim field updated', `${FIELD_LABELS[field]}: ${value}`);
      appendToolEvent('update_claim_field', 'complete', `${FIELD_LABELS[field]} captured`, `${field}: ${value}`);
      if (field === 'injuryStatus') {
        setWorkflowStatus('Confirm safety and injuries', 'Complete', value);
      }
      if (field === 'policeReportStatus' && value.toLowerCase().includes('need')) {
        setWorkflowStatus('Collect police report', 'Missing', 'Police report can be provided later');
      }
      setWorkflowStatus('Collect incident fields', 'Active', `${completedCount + 1} fields captured`);
    },
    [addEvent, appendToolEvent, completedCount, flashField, setWorkflowStatus]
  );

  const highlightMissing = useCallback(
    (field: ClaimFieldKey) => {
      setClaim((current) => ({
        ...current,
        fields: {
          ...current.fields,
          [field]: {
            ...current.fields[field],
            status: 'Needed',
            tone: 'warn',
            missing: true,
            updated: false,
            note: 'Assistant still needs this detail',
          },
        },
      }));
      flashField(field);
      setToast(`${FIELD_LABELS[field]} still needed`);
      addEvent('Missing detail flagged', FIELD_LABELS[field]);
      appendToolEvent('highlight_missing', 'complete', `${FIELD_LABELS[field]} flagged`);
      setSupervisor((current) => ({
        ...current,
        blockers: Array.from(new Set([...current.blockers.filter(Boolean), FIELD_LABELS[field]])),
        triageRiskPercent: Math.max(current.triageRiskPercent, field === 'policeReportStatus' ? 36 : 30),
        triageRiskLevel: field === 'policeReportStatus' ? 'Medium' : current.triageRiskLevel,
        triageSignals: Array.from(
          new Set([...current.triageSignals, `${FIELD_LABELS[field]} is still missing`])
        ),
        recommendation:
          field === 'policeReportStatus'
            ? 'Request police report number before automated fault/excess estimate.'
            : current.recommendation,
      }));
      if (field === 'policeReportStatus') {
        setWorkflowStatus('Collect police report', 'Missing', 'Police report number is still needed');
      }
    },
    [addEvent, appendToolEvent, flashField, setWorkflowStatus]
  );

  const appendDialDigit = useCallback((digit: string) => {
    setDialedNumber((current) => (current.length >= 12 ? current : `${current}${digit}`));
  }, []);

  const deleteDialDigit = useCallback(() => {
    setDialedNumber((current) => current.slice(0, -1));
  }, []);

  const startPhoneCall = useCallback(() => {
    const number = dialedNumber.trim() || CLAIMS_PHONE_NUMBER;
    const requestId = callConnectionRequestRef.current + 1;
    callConnectionRequestRef.current = requestId;
    setDialedNumber(number);
    setAppPhase('connecting');
    setToast('Connecting to Northstar claims');
    addEvent('Claims number dialed', number);
    addTranscript('Customer', `Calling Northstar claims on ${number}.`);
    schedule(() => {
      if (callConnectionRequestRef.current !== requestId) return;
      setAppPhase('call');
      setToast('ClaimPilot call connected');
      addEvent('ClaimPilot call connected', 'Pipecat voice session ready');
      addTranscript('Assistant', CLAIMPILOT_PHONE_PROMPT);
    }, PHONE_CONNECTION_DELAY_MS);
  }, [addEvent, addTranscript, dialedNumber, schedule]);

  const startPhoneCallWithDaily = useCallback(() => {
    startPhoneCall();
    void connectPipecat().catch((error) => {
      console.warn('[Pipecat] Daily transport connect failed from phone dial action', error);
      setToast('Daily voice connection failed');
    });
  }, [connectPipecat, startPhoneCall]);

  const sendSecureLink = useCallback(
    (phoneNumber = '+61 *** *** 482', secureLink?: string, claimId?: string) => {
      const resolvedClaimId = claimId ?? extractClaimIdFromLink(secureLink) ?? claim.claimId;
      const resolvedSecureLink = secureLink ?? secureClaimLink(resolvedClaimId);

      setShowSmsNotification(false);
      addEvent('Secure claim link queued', `Sending to ${phoneNumber}`);
      appendToolEvent(
        'send_secure_link',
        'running',
        `Secure link queued for ${phoneNumber}`,
        resolvedSecureLink
      );

      schedule(() => {
        setClaim((current) => ({
          ...current,
          claimId: resolvedClaimId,
        }));
        setAppPhase('secureLink');
        setShowSmsNotification(true);
        setToast('Secure claim link sent');
        addEvent('Secure claim link sent', resolvedSecureLink);
        addTranscript('System', `Northstar SMS delivered: ${resolvedSecureLink}`);
        appendToolEvent(
          'send_secure_link',
          'complete',
          `Secure link sent to ${phoneNumber}`,
          resolvedSecureLink
        );
      }, SECURE_LINK_DELIVERY_DELAY_MS);
    },
    [addEvent, addTranscript, appendToolEvent, claim.claimId, schedule]
  );

  const startAutoClaim = useCallback(() => {
    setAppPhase('app');
    setClaim((current) => ({
      ...current,
      claimType: 'Auto accident',
      status: 'Draft',
      customerSummary: 'Auto accident claim intake started. Details will populate as the assistant listens.',
      nextActions: ['Capture incident details', 'Collect evidence'],
    }));
    setSupervisor((current) => ({
      ...current,
      currentStep: 'Collect incident fields',
      blockers: [],
      triageRiskPercent: 38,
      triageRiskLevel: 'Medium',
      triageSignals: ['Customer call connected through Deepgram live voice', 'Incident details still incomplete'],
      previousClaimHistory: 'Northstar mock profile lookup pending',
      recommendation: 'Let Pipecat collect the first incident statement and safety check.',
      workflow: current.workflow.map((item) =>
        item.step === 'Collect incident fields'
          ? { ...item, status: 'Active', detail: 'Pipecat is listening for claim details' }
          : item
      ),
    }));
    appendToolEvent('Deepgram live voice', 'complete', 'Customer call connected');
    addEvent('Claim draft opened', 'Auto accident selected');
    addTranscript('Assistant', 'Your secure Northstar claim session is ready.');
    navigate('details', 'Claim draft started');
  }, [addEvent, addTranscript, appendToolEvent, navigate]);

  const completeSecureHandoff = useCallback(() => {
    setAppPhase('verified');
    setToast('Northstar session verified');
    addEvent('Secure link opened', 'Verified Northstar app session');
    addTranscript('System', 'Customer opened secure Northstar app link.');
    sendAppEvent('secure_handoff_completed', {
      claimId: claim.claimId,
      secureLink: secureClaimLink(claim.claimId),
      authenticated: true,
    });
    schedule(() => {
      startAutoClaim();
      addTranscript('Assistant', 'You are in. First, are you safe right now, and does anyone need urgent medical help?');
    }, 1100);
  }, [addEvent, addTranscript, claim.claimId, schedule, sendAppEvent, startAutoClaim]);

  const openClaimDetails = useCallback(
    (incidentSummary?: string, safetySummary?: string) => {
      setClaim((current) => ({
        ...current,
        claimType: 'Auto accident',
        status: 'Draft',
        whatHappenedSummary:
          incidentSummary ??
          (current.whatHappenedSummary || 'Customer reported a rear-end collision during the call.'),
        safetySummary:
          safetySummary ??
          (current.safetySummary ||
            'Customer reports being shaken up but does not think anyone is hurt.'),
        customerSummary:
          incidentSummary ??
          (current.customerSummary ||
            'Customer reported a rear-end collision during the claim intake call.'),
        timeline: current.timeline.length > 0 ? current.timeline : SAMPLE_TIMELINE,
        nextActions: ['Complete claim details', 'Collect evidence'],
      }));
      appendToolEvent('open_claim_details', 'complete', 'Northstar claim details opened from Pipecat tool call');
      appendToolEvent('update_claim_summary', 'complete', 'Voice prefill summary stored');
      setWorkflowStatus('Collect incident fields', 'Complete', 'Core incident details captured');
      setWorkflowStatus('Build timeline', 'Complete', 'Initial timeline extracted from voice intake');
      setSupervisor((current) => ({
        ...current,
        currentStep: 'Collect photos',
        blockers: current.blockers.filter((blocker) => blocker !== 'Claim intake has not started'),
        triageRiskPercent: 31,
        triageRiskLevel: 'Medium',
        triageSignals: [
          'Safety statement is clear',
          'Timeline extracted from voice intake',
          'Evidence and police report still needed',
        ],
        previousClaimHistory: 'No prior claim history in mock record',
        recommendation: 'Continue intake and request rear bumper photo after vehicle details are captured.',
      }));
      addEvent('Claim details opened', 'What happened page pre-filled from the call');
      navigate('details', 'Claim details opened');
    },
    [addEvent, appendToolEvent, navigate, setWorkflowStatus]
  );

  const requestEvidence = useCallback(
    (requestedEvidence = 'Rear bumper photo', options: EvidenceRequestOptions = {}) => {
      setClaim((current) => ({
        ...current,
        requestedEvidence,
        evidenceStatus: 'waiting',
        evidenceId: options.evidenceId ?? current.evidenceId,
        evidenceUploadUrl: options.uploadUrl ?? current.evidenceUploadUrl,
        evidenceS3Key: options.s3Key ?? current.evidenceS3Key,
        evidenceStorage: options.storage ?? current.evidenceStorage,
      }));
      navigate('evidence', 'Evidence requested');
      addEvent('Evidence requested', requestedEvidence);
      appendToolEvent('request_evidence', 'complete', `${requestedEvidence} requested`);
      setWorkflowStatus('Collect photos', 'Active', `${requestedEvidence} requested`);
      if (options.s3Key || options.storage) {
        const storageDetail = options.s3Key ?? options.storage ?? 'Upload target ready';
        addEvent('Evidence storage prepared', storageDetail);
        appendAwsProof('S3', storageDetail, 'presigned upload target');
      }
      addTranscript('Assistant', `Please upload a clear ${requestedEvidence.toLowerCase()} so I can add it to the claim.`);
    },
    [addEvent, addTranscript, appendAwsProof, appendToolEvent, navigate, setWorkflowStatus]
  );

  const showEvidenceResult = useCallback(
    (
      value = 'Rear bumper damage visible, moderate severity',
      severity = 'Moderate',
      options: Pick<EvidenceRequestOptions, 's3Key' | 'storage' | 'analysisProvider' | 'notes'> = {}
    ) => {
      const isMismatch = isEvidenceMismatchResult(value, severity, options.notes);
      evidenceAnalysisRequestRef.current += 1;
      setClaim((current) => ({
        ...current,
        evidenceStatus: isMismatch ? 'rejected' : 'analyzed',
        evidenceResult: value,
        evidenceSeverity: isMismatch ? 'Needs new photo' : severity,
        evidenceAnalysisProvider:
          options.analysisProvider ?? (current.evidenceAnalysisProvider || 'ClaimPilot demo fallback'),
        evidenceNotes: isMismatch
          ? options.notes ?? ['Photo does not match the requested vehicle damage', 'Please upload the damaged bumper photo']
          : options.notes ?? current.evidenceNotes,
        evidenceFileName: current.evidenceFileName || 'rear-bumper-photo.jpg',
        evidenceS3Key: options.s3Key ?? current.evidenceS3Key,
        evidenceStorage:
          options.storage ??
          (current.evidenceStorage || current.evidenceS3Key || 'local-demo://rear-bumper-photo.jpg'),
        preSubmitSummary:
          isMismatch
            ? current.preSubmitSummary
            : current.preSubmitSummary ||
          'Auto claim packet is ready for review. The customer reported a rear-end collision near Norton Street, no injuries, and rear bumper photo evidence has been attached.',
        adjusterSummary:
          isMismatch
            ? current.adjusterSummary
            : 'Customer reports a rear-end collision near Norton Street with visible rear bumper damage. No injuries reported. Other driver left the scene. Police report number still needed.',
        customerSummary:
          isMismatch
            ? current.customerSummary
            : 'Customer reports being rear-ended about 20 minutes ago near Norton Street. They are shaken up but report no injuries.',
      }));
      navigate(isMismatch ? 'evidence' : 'review', isMismatch ? 'New photo needed' : 'Evidence analyzed');
      addEvent('Evidence analyzed', value.split(',')[0] ?? value);
      appendToolEvent('analyze_evidence', isMismatch ? 'failed' : 'complete', value.split(',')[0] ?? value);
      appendAwsProof(
        options.analysisProvider?.includes('Bedrock') ? 'Bedrock vision' : 'AWS image analysis',
        value,
        options.analysisProvider ?? 'demo analysis'
      );
      setWorkflowStatus(
        'Collect photos',
        isMismatch ? 'Active' : 'Complete',
        isMismatch ? 'Photo did not match requested damage' : 'Evidence attached to claim packet'
      );
      if (!isMismatch) {
        setWorkflowStatus('Review claim summary', 'Active', 'Pre-submit summary generated');
      }
      setSupervisor((current) => ({
        ...current,
        currentStep: isMismatch ? 'Collect photos' : 'Review claim summary',
        blockers: isMismatch
          ? Array.from(new Set([...current.blockers, 'Matching damage photo']))
          : current.blockers.filter((blocker) => blocker !== 'Evidence not attached' && blocker !== 'Matching damage photo'),
        triageRiskPercent: isMismatch
          ? Math.max(current.triageRiskPercent, 46)
          : current.triageRiskPercent > 28
            ? 28
            : current.triageRiskPercent,
        triageRiskLevel: isMismatch ? 'Medium' : current.triageRiskPercent > 34 ? 'Medium' : 'Low',
        triageSignals: isMismatch
          ? [
              'Uploaded photo does not match requested damage',
              'Safety statement is clear',
              'Replacement evidence needed',
            ]
          : [
              'Evidence attached and analyzed',
              'Safety statement is clear',
              'Police report number still needed',
              'Previous claim history clean',
            ],
        recommendation: isMismatch
          ? 'Ask the customer to upload the actual damaged bumper photo before review.'
          : 'Review the generated claim packet and submit for auto physical damage review.',
      }));
      addTranscript(
        'Assistant',
        isMismatch
          ? 'That photo does not match the requested vehicle damage. Please upload the damaged bumper photo instead.'
          : 'The photo analysis is back. It shows bumper damage, and I have attached it to the claim.'
      );
    },
    [addEvent, addTranscript, appendAwsProof, appendToolEvent, navigate, setWorkflowStatus]
  );

  const showFinalPacket = useCallback(
    (
      claimId = 'CLM-1049',
      options: {
        customerSummary?: string;
        adjusterSummary?: string;
        recommendedNextAction?: string;
        customerFollowUp?: string;
        storage?: string;
      } = {}
    ) => {
      const fallbackSmsText =
        options.customerFollowUp ??
        `Claim ${claimId} submitted. Summary: ${options.customerSummary ?? claim.preSubmitSummary ?? claim.customerSummary}. We received your photo evidence. Still needed: ${claim.missingInformation.join(', ')}.`;
      setClaimSmsSent(true);
      setClaimSmsText(fallbackSmsText);
      setClaim((current) => ({
        ...current,
        claimId,
        status: 'Submitted',
        customerSummary: options.customerSummary ?? current.customerSummary,
        adjusterSummary: options.adjusterSummary ?? current.adjusterSummary,
        customerFollowUp:
          options.customerFollowUp ??
          fallbackSmsText,
        finalPacketStorage: options.storage ?? current.finalPacketStorage,
        nextActions: [
          options.recommendedNextAction ?? 'Route to auto physical damage review',
          'Request police report number',
          "Follow up for other driver's details",
        ],
      }));
      navigate('submitted', 'Claim submitted');
      setAppPhase('claimSms');
      addEvent('Claim submitted', `${claimId} routed to auto physical damage review`);
      appendToolEvent('create_claim', 'complete', `${claimId} created`);
      appendAwsProof('DynamoDB', `ClaimPilotClaims / ${claimId}`, 'claim item');
      appendAwsProof('S3', options.storage ?? `claims/${claimId}/final-packet.json`, 'final packet');
      setWorkflowStatus('Review claim summary', 'Complete', 'Customer reviewed the packet');
      setWorkflowStatus('Submit claim', 'Complete', `${claimId} submitted`);
      setSupervisor((current) => ({
        ...current,
        currentStep: 'Submitted',
        blockers: current.blockers.filter((blocker) => !blocker.includes('Evidence')),
        triageRiskPercent: Math.max(current.triageRiskPercent, 36),
        triageRiskLevel: 'Medium',
        recommendation: 'Route to auto physical damage review and collect open follow-up details.',
      }));
      addTranscript('Assistant', `Your claim is submitted as ${claimId}. A claims specialist will review the packet and evidence.`);
    },
    [addEvent, addTranscript, appendAwsProof, appendToolEvent, claim.customerSummary, claim.missingInformation, claim.preSubmitSummary, navigate, setWorkflowStatus]
  );

  const sendClaimSms = useCallback(
    (
      claimId: string,
      customerFollowUp?: string,
      customerSummary?: string,
      phoneNumber = '+61 *** *** 482'
    ) => {
      const smsText =
        customerFollowUp ||
        `Claim ${claimId} submitted. Summary: ${customerSummary || claim.preSubmitSummary || claim.customerSummary || 'Your claim details and evidence were received.'}`;

      setClaimSmsSent(true);
      setClaimSmsText(smsText);
      setAppPhase('claimSms');
      setClaim((current) => ({
        ...current,
        customerFollowUp: smsText,
      }));
      addEvent('Claim summary SMS sent', `${claimId} summary sent to ${phoneNumber}`);
      appendToolEvent('send_claim_sms', 'complete', `Claim summary sent to ${phoneNumber}`, claimId);
      addTranscript('System', `Northstar SMS delivered: ${smsText}`);
    },
    [addEvent, addTranscript, appendToolEvent, claim.customerSummary, claim.preSubmitSummary]
  );

  const simulateEvidenceUpload = useCallback(
    (
      fileName = 'rear-bumper-photo.jpg',
      imageUrl?: string,
      file?: File,
      uploadOptions: EvidenceRequestOptions = {}
    ) => {
      const evidenceId = uploadOptions.evidenceId ?? claim.evidenceId;
      const evidenceUploadUrl = uploadOptions.uploadUrl ?? claim.evidenceUploadUrl;
      const evidenceS3Key = uploadOptions.s3Key ?? claim.evidenceS3Key;
      const evidenceStorage = uploadOptions.storage ?? claim.evidenceStorage;
      const evidenceContentType = uploadOptions.contentType ?? file?.type ?? 'image/jpeg';
      const isDemoMismatch =
        Boolean(uploadOptions.evidenceMismatch) || looksLikeUnrelatedEvidence(fileName);

      if (!claim.requestedEvidence) {
        setClaim((current) => ({ ...current, requestedEvidence: 'Rear bumper photo' }));
      }
      setClaim((current) => ({
        ...current,
        evidenceStatus: 'uploading',
        evidenceFileName: fileName,
        selectedImageUrl: imageUrl ?? (file ? current.selectedImageUrl : ''),
      }));
      setToast('Uploading evidence');
      addEvent('Photo selected', fileName);
      appendToolEvent('browser_evidence_upload', 'running', fileName);

      const uploadDetails = {
        evidenceId,
        s3Key: evidenceS3Key,
        storage: evidenceStorage,
        fileName,
        requestedEvidence: claim.requestedEvidence || 'Rear bumper photo',
        contentType: evidenceContentType,
        fileSize: file?.size,
      };
      const uploadToken = evidenceAnalysisRequestRef.current + 1;
      evidenceAnalysisRequestRef.current = uploadToken;
      let uploadCompletionEventSent = false;

      const runLocalDemoAnalysis = () => {
        schedule(() => {
          setClaim((current) => ({ ...current, evidenceStatus: 'analyzing' }));
          setToast('Analyzing image');
        }, 700);
        schedule(() => {
          if (evidenceAnalysisRequestRef.current !== uploadToken) return;
          if (isDemoMismatch) {
            showEvidenceResult(
              'Photo does not show the requested bumper damage',
              'Needs new photo',
              {
                analysisProvider: 'ClaimPilot evidence validation',
                notes: [
                  'Uploaded image is unrelated to the damage request',
                  `Requested: ${claim.requestedEvidence || 'Rear bumper photo'}`,
                  'Ask customer to upload the damaged bumper photo',
                ],
              }
            );
            return;
          }
          showEvidenceResult();
        }, 1500);
      };

      if (isDemoMismatch) {
        appendToolEvent('browser_evidence_upload', 'complete', 'Evidence attached for validation');
        appendAwsProof('Local demo', `local-demo://${fileName}`, 'mismatched evidence object');
        runLocalDemoAnalysis();
        schedule(() => {
          setClaim((current) =>
            current.evidenceStatus === 'analyzing' || current.evidenceStatus === 'analyzed' || current.evidenceStatus === 'rejected'
              ? current
              : { ...current, evidenceStatus: 'attached' }
          );
          setToast('Checking uploaded photo');
          setWorkflowStatus('Collect photos', 'Active', 'Validating uploaded photo');
        }, 800);
        return;
      }

      const notifyUploadCompleted = (details: typeof uploadDetails & Record<string, unknown>) => {
        const eventSent = sendAppEvent('evidence_upload_completed', details);
        uploadCompletionEventSent = eventSent;
        if (!eventSent) {
          runLocalDemoAnalysis();
        } else {
          setClaim((current) => ({ ...current, evidenceStatus: 'analyzing' }));
          setToast('Analyzing image with Bedrock');
          appendToolEvent('analyze_evidence', 'running', 'Waiting for Bedrock vision result from server');
          schedule(() => {
            if (evidenceAnalysisRequestRef.current !== uploadToken) return;
            setClaim((current) =>
              current.evidenceStatus === 'analyzed'
                ? current
                : { ...current, evidenceStatus: 'analyzing' }
            );
            setToast('Image analysis still pending');
            addEvent('Image analysis pending', 'Waiting for Bedrock vision result from server');
            appendToolEvent('analyze_evidence', 'running', 'Bedrock vision analysis still pending');
          }, 25000);
        }
      };

      if (file && evidenceUploadUrl) {
        fetch(evidenceUploadUrl, {
          method: 'PUT',
          headers: { 'Content-Type': evidenceContentType },
          body: file,
        })
          .then((response) => {
            if (!response.ok) throw new Error(`S3 upload returned ${response.status}`);
            addEvent('Uploaded to S3', evidenceS3Key || 'Evidence object stored');
            appendToolEvent('browser_evidence_upload', 'complete', 'Evidence uploaded to S3');
            appendAwsProof('S3', evidenceS3Key || evidenceStorage, 'evidence object');
            notifyUploadCompleted({
              ...uploadDetails,
              uploadTarget: 's3',
            });
          })
          .catch((error: unknown) => {
            console.warn('[ClaimPilot] S3 upload failed; continuing with local preview', error);
            addEvent('S3 upload fallback', 'Continuing with local preview for demo');
            appendToolEvent('browser_evidence_upload', 'failed', 'S3 upload failed; local preview retained');
            sendAppEvent('evidence_upload_failed', {
              ...uploadDetails,
              reason: error instanceof Error ? error.message : 'Browser S3 upload failed',
            });
          });
      } else if (evidenceId || evidenceS3Key || evidenceStorage) {
        const hasS3EvidenceTarget =
          Boolean(evidenceS3Key) || evidenceStorage.startsWith('s3://');
        if (hasS3EvidenceTarget) {
          appendToolEvent('browser_evidence_upload', 'complete', 'Evidence attached in local demo');
          appendAwsProof(
            'S3',
            evidenceS3Key || evidenceStorage,
            'evidence object'
          );
          notifyUploadCompleted({
            ...uploadDetails,
            storage: evidenceStorage || evidenceS3Key,
            uploadTarget: 'local-demo',
          });
        } else {
          appendToolEvent('browser_evidence_upload', 'complete', 'Evidence attached in local demo');
          appendAwsProof('Local demo', `local-demo://${fileName}`, 'sample evidence object');
          runLocalDemoAnalysis();
        }
      } else {
        appendToolEvent('browser_evidence_upload', 'complete', 'Evidence attached in local demo');
        appendAwsProof('Local demo', `local-demo://${fileName}`, 'sample evidence object');
        runLocalDemoAnalysis();
      }

      schedule(() => {
        setClaim((current) =>
          current.evidenceStatus === 'analyzing' || current.evidenceStatus === 'analyzed'
            ? current
            : { ...current, evidenceStatus: 'attached' }
        );
        setToast((current) =>
          uploadCompletionEventSent
            ? current
            : 'Evidence attached'
        );
        setWorkflowStatus('Collect photos', 'Complete', 'Evidence attached');
      }, 800);

    },
    [
      addEvent,
      claim.evidenceId,
      claim.evidenceS3Key,
      claim.evidenceStorage,
      claim.evidenceUploadUrl,
      claim.requestedEvidence,
      appendAwsProof,
      appendToolEvent,
      schedule,
      sendAppEvent,
      showEvidenceResult,
      setWorkflowStatus,
    ]
  );

  const processEvidenceFile = useCallback(
    (file: File) => {
      const objectUrl = URL.createObjectURL(file);
      objectUrlsRef.current.push(objectUrl);

      if (!claim.evidenceUploadUrl) {
        const requestedEvidence = claim.requestedEvidence || 'Rear bumper photo';
        pendingEvidenceUploadRef.current = {
          file,
          fileName: file.name,
          imageUrl: objectUrl,
          requestedEvidence,
        };
        setClaim((current) => ({
          ...current,
          requestedEvidence: current.requestedEvidence || requestedEvidence,
          evidenceStatus: 'uploading',
          evidenceFileName: file.name,
          selectedImageUrl: objectUrl,
        }));
        setToast('Preparing S3 upload');
        addEvent('Photo selected', file.name);
        appendToolEvent('request_evidence', 'running', 'Requesting S3 upload target');
        setEventBridgeEnabled(true);
        const eventSent = sendPendingEvidenceUploadRequest();
        if (eventSent) return;
        schedule(() => {
          if (!pendingEvidenceUploadRef.current) return;
          if (eventSocketRef.current?.readyState === WebSocket.OPEN) return;
          pendingEvidenceUploadRef.current = null;
          addEvent('UI event bridge unavailable', 'Using local demo evidence analysis');
          appendToolEvent('request_evidence', 'failed', 'UI event bridge unavailable; using local demo');
          simulateEvidenceUpload(file.name, objectUrl, file);
        }, 1800);
        schedule(() => {
          if (!pendingEvidenceUploadRef.current) return;
          pendingEvidenceUploadRef.current = null;
          addEvent('S3 upload target timeout', 'Using local demo evidence analysis');
          appendToolEvent('request_evidence', 'failed', 'Timed out waiting for S3 upload target; using local demo');
          simulateEvidenceUpload(file.name, objectUrl, file);
        }, 6000);
        return;
      }

      if (eventSocketRef.current?.readyState === WebSocket.OPEN) {
        const eventSent = sendPendingEvidenceUploadRequest();
        if (eventSent) return;
        pendingEvidenceUploadRef.current = null;
      }

      simulateEvidenceUpload(file.name, objectUrl, file);
    },
    [
      addEvent,
      appendToolEvent,
      claim.evidenceUploadUrl,
      claim.requestedEvidence,
      schedule,
      sendPendingEvidenceUploadRequest,
      simulateEvidenceUpload,
    ]
  );

  const handleEvidenceFile = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) return;

      processEvidenceFile(file);
      event.currentTarget.value = '';
    },
    [processEvidenceFile]
  );

  const handleEvidenceDrop = useCallback(
    (event: DragEvent<HTMLLabelElement>) => {
      event.preventDefault();
      const file = Array.from(event.dataTransfer.files).find((candidate) =>
        candidate.type.startsWith('image/')
      );
      if (file) processEvidenceFile(file);
    },
    [processEvidenceFile]
  );

  const resetDemo = useCallback(() => {
    clearTimers();
    objectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    objectUrlsRef.current = [];
    evidenceAnalysisRequestRef.current += 1;
    setAppPhase('dialer');
    setDialedNumber('');
    setShowSmsNotification(false);
    setClaimSmsSent(false);
    setClaimSmsText('');
    setScreen('start');
    setClaim(emptyClaim());
    setSupervisor(emptySupervisor());
    setToolEvents(START_TOOL_EVENTS);
    setAwsProof([]);
    setEvents(START_EVENTS);
    setTranscript(START_TRANSCRIPT);
    setEventBridgeEnabled(false);
    setFreshFields(new Set());
    setToast('');
    callConnectionRequestRef.current += 1;
  }, [clearTimers]);

  const showCorrection = useCallback(() => {
    navigate('details', 'Assistant captured vehicle');
    addTranscript('Customer', 'It was my Civic.');
    updateClaimField('vehicle', 'Honda Civic · Owner: customer', {
      status: 'Captured',
      tone: 'success',
      updated: false,
      note: 'Captured earlier in call',
      toast: 'Assistant captured vehicle',
    });

    schedule(() => {
      addTranscript('Customer', "Actually, it was my wife's Corolla, not my Civic.");
      updateClaimField('vehicle', 'Toyota Corolla · Owner: spouse', {
        status: 'Updated',
        tone: 'neutral',
        updated: true,
        note: 'Updated from my Civic during the call',
        toast: 'Assistant corrected vehicle',
      });
      addEvent('Vehicle corrected', "Updated from Civic to spouse's Corolla");
    }, 950);
  }, [addEvent, addTranscript, navigate, schedule, updateClaimField]);

  const runDemo = useCallback(() => {
    resetDemo();

    schedule(() => {
      setDialedNumber(CLAIMS_PHONE_NUMBER);
      startPhoneCall();
    }, 450);

    schedule(() => {
      addTranscript('Customer', 'Yes, please.');
      sendSecureLink();
    }, 1700);

    schedule(() => {
      completeSecureHandoff();
    }, 2850);

    schedule(() => {
      addTranscript('Customer', 'Yes, I am safe and no one needs urgent medical help.');
      addTranscript('Assistant', 'I am glad you are safe. In your own words, what happened?');
      updateClaimField('injuryStatus', 'No injuries reported', {
        status: 'Captured',
        tone: 'success',
        note: 'Safety check complete',
        toast: 'Safety check complete',
      });
    }, 4550);

    schedule(() => {
      addTranscript('Customer', 'I was rear-ended about 20 minutes ago. I am shaken up but I do not think I am hurt.');
      addTranscript('Assistant', 'Thanks. I have the first version. I am opening the claim details so we can fill the rest together.');
      updateClaimField('incidentType', 'Rear-end collision', {
        status: 'Captured',
        tone: 'success',
        note: 'Captured from voice',
        toast: 'Incident captured',
      });
      updateClaimField('incidentTime', '20 minutes ago', {
        status: 'Captured',
        tone: 'success',
        note: 'Captured from voice',
        toast: 'Incident time captured',
      });
      openClaimDetails(
        'Customer reports being rear-ended about 20 minutes ago.',
        'Customer is shaken up but does not think they are hurt.'
      );
    }, 6100);

    schedule(() => {
      addTranscript('Assistant', 'Where are you, and what vehicle were you driving?');
      updateClaimField('location', 'Parramatta Road, near Norton Street', {
        status: 'Filled',
        tone: 'success',
        note: 'Captured from voice',
      });
      updateClaimField('damageArea', 'Rear bumper', {
        status: 'Filled',
        tone: 'success',
        note: 'Captured from voice',
      });
      updateClaimField('otherPartyStatus', 'Other driver left the scene', {
        status: 'Filled',
        tone: 'success',
        note: 'Captured from voice',
      });
    }, 7600);

    schedule(showCorrection, 8950);

    schedule(() => {
      navigate('people');
      highlightMissing('policeReportStatus');
    }, 10800);

    schedule(() => {
      requestEvidence('Rear bumper photo');
    }, 12100);

    schedule(() => {
      simulateEvidenceUpload();
    }, 13600);

    schedule(() => {
      showFinalPacket('CLM-1049');
    }, 17100);
  }, [
    addTranscript,
    completeSecureHandoff,
    highlightMissing,
    navigate,
    openClaimDetails,
    requestEvidence,
    resetDemo,
    schedule,
    sendSecureLink,
    showCorrection,
    showFinalPacket,
    simulateEvidenceUpload,
    startPhoneCall,
    updateClaimField,
  ]);

  const dispatchUiAction = useCallback(
    (rawAction: UiAction) => {
      const action = rawAction.type === 'ui_action' ? rawAction.action : rawAction.action;
      const payloadScreen = getPayloadString(rawAction, 'screen');
      const payloadField = getPayloadString(rawAction, 'field');
      const payloadValue = getPayloadString(rawAction, 'value');
      const payloadNote = getPayloadString(rawAction, 'note');
      const payloadClaimId = getPayloadString(rawAction, 'claimId');
      const payloadEvidence = getPayloadString(rawAction, 'requestedEvidence');
      const payloadIncidentSummary = getPayloadString(rawAction, 'incidentSummary');
      const payloadSafetySummary = getPayloadString(rawAction, 'safetySummary');
      const payloadSummary = getPayloadString(rawAction, 'summary');
      const payloadCustomerSummary = getPayloadString(rawAction, 'customerSummary');
      const payloadAdjusterSummary = getPayloadString(rawAction, 'adjusterSummary');
      const payloadRecommendedNextAction = getPayloadString(rawAction, 'recommendedNextAction');
      const payloadCustomerFollowUp = getPayloadString(rawAction, 'customerFollowUp');
      const payloadSeverity = getPayloadString(rawAction, 'severity');
      const payloadEvidenceId = getPayloadString(rawAction, 'evidenceId');
      const payloadUploadUrl = getPayloadString(rawAction, 'uploadUrl');
      const payloadPhoneNumber = getPayloadString(rawAction, 'phoneNumber');
      const payloadSecureLink = getPayloadString(rawAction, 'secureLink');
      const payloadS3Key = getPayloadString(rawAction, 's3Key');
      const payloadStorage = getPayloadString(rawAction, 'storage');
      const payloadContentType = getPayloadString(rawAction, 'contentType');
      const payloadAnalysisProvider = getPayloadString(rawAction, 'analysisProvider');
      const payloadNotes = getPayloadStrings(rawAction, 'notes');
      const payloadTool = getPayloadString(rawAction, 'tool');
      const payloadStatus = getPayloadString(rawAction, 'status');
      const payloadDetail = getPayloadString(rawAction, 'detail');
      const payloadPayloadPreview = getPayloadString(rawAction, 'payloadPreview');
      const payloadService = getPayloadString(rawAction, 'service');
      const payloadLabel = getPayloadString(rawAction, 'label');
      const payloadFaultDecision = getPayloadString(rawAction, 'faultDecision');
      const payloadExcessEstimate = getPayloadString(rawAction, 'excessEstimate');
      const payloadExplanation = getPayloadString(rawAction, 'explanation');
      const payloadReason = getPayloadString(rawAction, 'reason');
      const payloadTimeline = getTimelinePayload(rawAction);
      const payloadSupervisor = getPayloadObject<SupervisorState>(rawAction, 'supervisor');
      const payloadRiskPercent = getPayloadNumber(rawAction, 'triageRiskPercent');
      const payloadFields = rawAction.fields ?? getPayloadFieldUpdates(rawAction);

      if (action === 'start_phone_call') startPhoneCall();

      if (action === 'start_claim') startAutoClaim();

      if (action === 'send_secure_link') {
        sendSecureLink(
          rawAction.phoneNumber ?? payloadPhoneNumber,
          rawAction.secureLink ?? payloadSecureLink,
          rawAction.claimId ?? payloadClaimId
        );
      }

      if (action === 'complete_secure_handoff') {
        completeSecureHandoff();
      }

      if (action === 'open_claim_details') {
        openClaimDetails(
          rawAction.incidentSummary ?? payloadIncidentSummary,
          rawAction.safetySummary ?? payloadSafetySummary
        );
      }

      if (action === 'navigate') {
        const nextScreen = resolveScreen(rawAction.screen ?? payloadScreen);
        if (nextScreen) navigate(nextScreen);
      }

      if (action === 'fill_field' || action === 'updateClaimField') {
        const field = resolveField(rawAction.field ?? payloadField);
        const value = rawAction.value ?? payloadValue;
        if (field && value) {
          updateClaimField(field, value, {
            status: rawAction.status ?? payloadStatus,
            note: rawAction.note ?? payloadNote,
          });
        }
      }

      if (action === 'update_claim_fields' && payloadFields) {
        payloadFields.forEach((item) => {
          const field = resolveField(item.field);
          if (!field || !item.value) return;
          updateClaimField(field, item.value, {
            status: item.status ?? 'Captured',
            tone: item.status?.toLowerCase().includes('need') ? 'warn' : 'success',
            missing: item.status?.toLowerCase().includes('need') ? true : false,
            note: item.note ?? 'Captured from voice',
          });
        });
      }

      if (action === 'update_timeline') {
        if (payloadTimeline) updateTimeline(payloadTimeline);
      }

      if (action === 'update_claim_summary') {
        const summary =
          rawAction.summary ?? payloadSummary ?? rawAction.customerSummary ?? payloadCustomerSummary;
        setClaim((current) => ({
          ...current,
          preSubmitSummary: summary ?? current.preSubmitSummary,
          customerSummary: rawAction.customerSummary ?? payloadCustomerSummary ?? summary ?? current.customerSummary,
          adjusterSummary: rawAction.adjusterSummary ?? payloadAdjusterSummary ?? current.adjusterSummary,
        }));
        if (summary) {
          addEvent('Claim summary generated', summary);
          appendToolEvent('update_claim_summary', 'complete', 'Pre-submit summary generated');
        }
      }

      if (action === 'update_supervisor_state') {
        mergeSupervisorState({
          ...(payloadSupervisor ?? {}),
          triageRiskPercent:
            payloadSupervisor?.triageRiskPercent ?? payloadRiskPercent ?? supervisor.triageRiskPercent,
        });
        appendToolEvent('update_supervisor_state', 'complete', 'Claims Supervisor state updated');
      }

      if (action === 'append_tool_event') {
        appendToolEvent(
          rawAction.tool ?? payloadTool ?? 'pipecat_tool',
          normalizeToolStatus(rawAction.status ?? payloadStatus),
          rawAction.detail ?? payloadDetail ?? 'Tool call event',
          rawAction.payloadPreview ?? payloadPayloadPreview
        );
      }

      if (action === 'append_aws_proof') {
        appendAwsProof(
          rawAction.service ?? payloadService ?? 'AWS',
          rawAction.detail ?? payloadDetail ?? '',
          rawAction.label ?? payloadLabel
        );
      }

      if (action === 'show_policy_answer') {
        const question = getPayloadString(rawAction, 'question') ?? 'Policy answer';
        const answer = getPayloadString(rawAction, 'answer') ?? rawAction.detail ?? payloadDetail ?? '';
        if (answer) {
          addEvent('Policy answer surfaced', question);
          addTranscript('System', answer);
        }
      }

      if (action === 'highlight_missing') {
        const field = resolveField(rawAction.field ?? payloadField);
        if (field) highlightMissing(field);
      }

      if (action === 'request_evidence_upload' || action === 'requestEvidence' || action === 'request_evidence') {
        const evidenceOptions = {
          evidenceId: rawAction.evidenceId ?? payloadEvidenceId,
          uploadUrl: rawAction.uploadUrl ?? payloadUploadUrl,
          s3Key: rawAction.s3Key ?? payloadS3Key,
          storage: rawAction.storage ?? payloadStorage,
          contentType: rawAction.contentType ?? payloadContentType,
        };
        requestEvidence(rawAction.requestedEvidence ?? payloadEvidence ?? 'Rear bumper photo', {
          ...evidenceOptions,
        });
        const pendingUpload = pendingEvidenceUploadRef.current;
        if (pendingUpload && evidenceOptions.uploadUrl) {
          pendingEvidenceUploadRef.current = null;
          schedule(() => {
            simulateEvidenceUpload(
              pendingUpload.fileName,
              pendingUpload.imageUrl,
              pendingUpload.file,
              evidenceOptions
            );
          }, 0);
        } else if (pendingUpload) {
          pendingEvidenceUploadRef.current = null;
          schedule(() => {
            simulateEvidenceUpload(
              pendingUpload.fileName,
              pendingUpload.imageUrl,
              pendingUpload.file
            );
          }, 0);
        }
      }

      if (action === 'show_evidence_result' || action === 'analyzeEvidence' || action === 'analyze_evidence') {
        showEvidenceResult(
          rawAction.value ?? payloadValue ?? 'Rear bumper damage visible, moderate severity',
          rawAction.severity ?? payloadSeverity ?? 'Moderate',
          {
            s3Key: rawAction.s3Key ?? payloadS3Key,
            storage: rawAction.storage ?? payloadStorage,
            analysisProvider: rawAction.analysisProvider ?? payloadAnalysisProvider,
            notes: rawAction.notes ?? payloadNotes,
          }
        );
      }

      if (action === 'show_fault_excess' || action === 'estimate_fault_and_excess') {
        setClaim((current) => ({
          ...current,
          faultDecision: {
            available: Boolean((rawAction.faultDecision ?? payloadFaultDecision) && (rawAction.excessEstimate ?? payloadExcessEstimate)),
            faultDecision: rawAction.faultDecision ?? payloadFaultDecision,
            excessEstimate: rawAction.excessEstimate ?? payloadExcessEstimate,
            explanation: rawAction.explanation ?? payloadExplanation,
            reason: rawAction.reason ?? payloadReason,
          },
        }));
        appendToolEvent('show_fault_excess', 'complete', rawAction.faultDecision ?? payloadFaultDecision ?? 'Manual review required');
      }

      if (action === 'show_final_packet' || action === 'createClaim' || action === 'showFinalPacket' || action === 'create_claim') {
        showFinalPacket(rawAction.claimId ?? payloadClaimId ?? 'CLM-1049', {
          customerSummary: rawAction.customerSummary ?? payloadCustomerSummary,
          adjusterSummary: rawAction.adjusterSummary ?? payloadAdjusterSummary,
          recommendedNextAction: rawAction.recommendedNextAction ?? payloadRecommendedNextAction,
          customerFollowUp: rawAction.customerFollowUp ?? payloadCustomerFollowUp,
          storage: rawAction.storage ?? payloadStorage,
        });
      }

      if (action === 'send_claim_sms') {
        sendClaimSms(
          rawAction.claimId ?? payloadClaimId ?? claim.claimId,
          rawAction.customerFollowUp ?? payloadCustomerFollowUp,
          rawAction.customerSummary ?? payloadCustomerSummary,
          rawAction.phoneNumber ?? payloadPhoneNumber
        );
      }
    },
    [
      addEvent,
      addTranscript,
      appendAwsProof,
      appendToolEvent,
      highlightMissing,
      navigate,
      openClaimDetails,
      requestEvidence,
      schedule,
      sendClaimSms,
      sendSecureLink,
      showEvidenceResult,
      showFinalPacket,
      simulateEvidenceUpload,
      startPhoneCall,
      startAutoClaim,
      completeSecureHandoff,
      claim.claimId,
      supervisor.triageRiskPercent,
      mergeSupervisorState,
      updateTimeline,
      updateClaimField,
    ]
  );

  const emitDemoActions = useCallback(
    (actions: UiAction[]) => {
      actions.forEach((action) => dispatchUiAction(action));
    },
    [dispatchUiAction]
  );

  const disconnectVoiceAgent = useCallback(() => {
    const hadClient = Boolean(voiceAgentClientRef.current);
    voiceAgentClientRef.current?.disconnect();
    voiceAgentClientRef.current = null;
    setVoiceAgentMuted(false);
    setVoiceAgentStatus(hadClient ? 'disconnected' : 'idle');
    setVoiceAgentDetail(
      hadClient
        ? 'Deepgram Voice Agent relay disconnected'
        : 'Deepgram Voice Agent relay not connected'
    );
  }, []);

  const connectVoiceAgent = useCallback(async () => {
    if (voiceAgentClientRef.current?.isConnected) return;

    const voiceAgentUrl =
      import.meta.env.VITE_CLAIMPILOT_VOICE_AGENT_WS_URL ?? 'ws://127.0.0.1:8790';

    const voiceAgent = new VoiceAgentClient({
      url: voiceAgentUrl,
      onStatus: (status, detail) => {
        setVoiceAgentStatus(status);
        setVoiceAgentDetail(detail ?? status);
        if (status === 'connected' || status === 'error' || status === 'disconnected') {
          addEvent('Voice Agent status', detail ?? status);
        }
      },
      onTranscript: (entry) => {
        if (entry.text) addTranscript(entry.speaker, entry.text);
      },
      onUiAction: (action) => {
        dispatchUiAction(action as UiAction);
      },
      onLatency: (event) => {
        setVoiceAgentLatency(event);
        appendToolEvent(
          event.metric,
          'complete',
          `${event.ms} ms${event.tttMs ? `, TTT ${event.tttMs} ms` : ''}${event.ttsMs ? `, TTS ${event.ttsMs} ms` : ''}`
        );
      },
      onEvent: (title, detail) => {
        addEvent(title, detail);
      },
    });

    voiceAgentClientRef.current = voiceAgent;
    try {
      await voiceAgent.connect();
      setVoiceAgentMuted(false);
      appendToolEvent('voice_agent_connect', 'complete', `Connected to ${voiceAgentUrl}`);
    } catch (error) {
      voiceAgent.disconnect();
      voiceAgentClientRef.current = null;
      setVoiceAgentStatus('error');
      setVoiceAgentDetail(error instanceof Error ? error.message : 'Voice Agent connection failed');
      appendToolEvent('voice_agent_connect', 'failed', 'Voice Agent relay connection failed');
    }
  }, [addEvent, addTranscript, appendToolEvent, dispatchUiAction]);

  const toggleVoiceAgentMuted = useCallback(() => {
    setVoiceAgentMuted((current) => {
      const next = !current;
      voiceAgentClientRef.current?.setMuted(next);
      return next;
    });
  }, []);

  const emitDetailsSample = useCallback(() => {
    emitDemoActions([
      { action: 'start_claim' },
      {
        action: 'open_claim_details',
        incidentSummary: 'Customer reports being rear-ended about 20 minutes ago near Norton Street.',
        safetySummary: 'Customer is shaken up but reports no injuries.',
      },
      { action: 'fill_field', field: 'incidentType', value: 'Rear-end collision' },
      { action: 'fill_field', field: 'incidentTime', value: '20 minutes ago' },
      { action: 'fill_field', field: 'location', value: 'Parramatta Road, near Norton Street' },
      { action: 'fill_field', field: 'vehicle', value: 'Toyota Corolla · Owner: spouse' },
      { action: 'fill_field', field: 'damageArea', value: 'Rear bumper' },
      { action: 'fill_field', field: 'injuryStatus', value: 'No injuries reported' },
      { action: 'fill_field', field: 'otherPartyStatus', value: 'Other driver left the scene' },
    ]);
  }, [emitDemoActions]);

  const emitTimelineSample = useCallback(() => {
    emitDemoActions([
      {
        action: 'update_timeline',
        events: [
          {
            id: 'demo-timeline-1',
            time: '20 minutes ago',
            description: 'Customer was driving near Norton Street when another vehicle hit the rear bumper.',
            source: 'Deepgram live voice',
            status: 'Captured',
          },
          {
            id: 'demo-timeline-2',
            time: 'After impact',
            description: 'Customer pulled over, confirmed no injuries, and started the ClaimPilot intake.',
            source: 'ClaimPilot safety check',
            status: 'Captured',
          },
          {
            id: 'demo-timeline-3',
            time: 'During call',
            description: "Vehicle corrected from customer's Civic to spouse's Toyota Corolla.",
            source: 'Pipecat correction handling',
            status: 'Updated',
            updated: true,
          },
        ],
      },
      {
        action: 'update_claim_summary',
        summary:
          'Toyota Corolla was involved in a rear-end collision near Norton Street about 20 minutes ago. The customer reports no injuries and rear bumper damage. Other driver details and police report number remain open.',
      },
    ]);
  }, [emitDemoActions]);

  const emitSupervisorSample = useCallback(() => {
    emitDemoActions([
      {
        action: 'update_supervisor_state',
        supervisor: {
          currentStep: 'Review claim summary',
          blockers: ['Police report number', "Other driver's license plate"],
          triageRiskPercent: 36,
          triageRiskLevel: 'Medium',
          triageSignals: [
            'Timeline is internally consistent',
            'Rear bumper evidence attached',
            'Police report number missing',
            'Other driver left the scene',
            'Previous claim history clean',
          ],
          previousClaimHistory: 'No prior claim history in mock record',
          recommendation: 'Submit for adjuster review and request police report number after intake.',
          workflow: [
            { step: 'Collect incident fields', status: 'Complete', detail: 'Core claim fields captured' },
            { step: 'Confirm safety and injuries', status: 'Complete', detail: 'No injuries reported' },
            { step: 'Build timeline', status: 'Complete', detail: 'Three events extracted' },
            { step: 'Collect photos', status: 'Active', detail: 'Rear bumper photo requested' },
            { step: 'Collect police report', status: 'Missing', detail: 'Police report number is still needed' },
            { step: 'Review claim summary', status: 'Active', detail: 'Pre-submit summary generated' },
            { step: 'Submit claim', status: 'Pending', detail: 'Waiting for customer confirmation' },
          ],
        },
      },
    ]);
  }, [emitDemoActions]);

  const emitEvidenceSample = useCallback(() => {
    emitDemoActions([
      {
        action: 'request_evidence_upload',
        requestedEvidence: 'Rear bumper photo',
        evidenceId: 'ev-demo-1049',
        s3Key: 'claims/CLM-1049/evidence/rear-bumper-photo.jpg',
        storage: 's3://claimpilot-demo/claims/CLM-1049/evidence/rear-bumper-photo.jpg',
      },
      {
        action: 'show_evidence_result',
        value: 'Rear bumper damage visible, moderate severity. No license plate visible in photo.',
        severity: 'Moderate',
        analysisProvider: 'Amazon Bedrock multimodal',
        notes: [
          'Visible vehicle part(s): rear bumper',
          'License plate not visible in photo',
          'Photo attached to adjuster packet',
          'Police report number still needed',
        ],
        s3Key: 'claims/CLM-1049/evidence/rear-bumper-photo.jpg',
      },
    ]);
  }, [emitDemoActions]);

  const emitSubmitSample = useCallback(() => {
    emitDemoActions([
      {
        action: 'show_fault_excess',
        reason: 'Fault and excess estimate unavailable until supervisor review is complete.',
      },
      {
        action: 'show_final_packet',
        claimId: 'CLM-1049',
        customerSummary:
          'Your auto claim has been submitted with a rear bumper photo attached. A claims specialist will review the packet.',
        adjusterSummary:
          'Customer reports a rear-end collision near Norton Street with visible rear bumper damage. No injuries reported. Other driver left the scene. Police report number still needed.',
        recommendedNextAction: 'Route to auto physical damage review',
        customerFollowUp:
          'Claim CLM-1049 submitted. We received your rear bumper photo. Still needed: police report number and other driver details.',
        storage: 'claims/CLM-1049/final-packet.json',
      },
      {
        action: 'send_claim_sms',
        claimId: 'CLM-1049',
        phoneNumber: '+61 *** *** 482',
        customerFollowUp:
          'Claim CLM-1049 submitted. We received your rear bumper photo. Still needed: police report number and other driver details.',
      },
    ]);
  }, [emitDemoActions]);

  const runEventDemo = useCallback(() => {
    resetDemo();
    schedule(emitDetailsSample, 350);
    schedule(emitTimelineSample, 1450);
    schedule(emitSupervisorSample, 2350);
    schedule(emitEvidenceSample, 3300);
    schedule(emitSubmitSample, 5550);
  }, [
    emitDetailsSample,
    emitEvidenceSample,
    emitSubmitSample,
    emitSupervisorSample,
    emitTimelineSample,
    resetDemo,
    schedule,
  ]);

  useEffect(() => {
    if (voiceRuntime === 'voice_agent') {
      setEventBridgeEnabled(false);
      eventSocketRef.current?.close();
      eventSocketRef.current = null;
      void pipecatDisconnectRef.current?.();
      return;
    }

    disconnectVoiceAgent();
  }, [disconnectVoiceAgent, voiceRuntime]);

  useEffect(() => {
    if (!eventBridgeEnabled || voiceRuntime !== 'pipecat') return;

    const eventUrl = import.meta.env.VITE_APP_EVENT_WS_URL ?? 'ws://127.0.0.1:8787';
    let stopped = false;
    let reconnectTimer: number | undefined;
    let attempt = 0;

    const connect = () => {
      if (stopped) return;

      const socket = new WebSocket(eventUrl);
      eventSocketRef.current = socket;

      socket.addEventListener('open', () => {
        attempt = 0;
        console.info(`[ClaimPilot] UI event stream connected: ${eventUrl}`);
        addEvent('UI event stream connected', eventUrl);
        sendPendingEvidenceUploadRequest();
      });

      socket.addEventListener('message', (event) => {
        try {
          const message = JSON.parse(event.data) as UiAction | UiAction[];
          const actions = Array.isArray(message) ? message : [message];
          actions.forEach((action) => dispatchUiAction(action));
        } catch (error) {
          console.warn('[ClaimPilot] Ignoring malformed UI event', error);
        }
      });

      socket.addEventListener('close', () => {
        if (eventSocketRef.current === socket) eventSocketRef.current = null;
        if (stopped) return;

        const delay = Math.min(1000 * 2 ** attempt, 5000);
        attempt += 1;
        console.info(`[ClaimPilot] UI event stream disconnected; reconnecting in ${delay}ms`);
        reconnectTimer = window.setTimeout(connect, delay);
      });

      socket.addEventListener('error', () => {
        socket.close();
      });
    };

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      eventSocketRef.current?.close();
      eventSocketRef.current = null;
    };
  }, [addEvent, dispatchUiAction, eventBridgeEnabled, sendPendingEvidenceUploadRequest, voiceRuntime]);

  useEffect(() => {
    if (!client) return;

    const pipecatClient = client as PipecatConsoleClient;
    pipecatClient.setLogLevel(LogLevel.DEBUG);

    const diagnosticEvents = [
      RTVIEvent.Connected,
      RTVIEvent.Disconnected,
      RTVIEvent.TransportStateChanged,
      RTVIEvent.BotStarted,
      RTVIEvent.BotConnected,
      RTVIEvent.BotReady,
      RTVIEvent.BotDisconnected,
      RTVIEvent.Error,
      RTVIEvent.MessageError,
      RTVIEvent.ServerMessage,
      RTVIEvent.Metrics,
      RTVIEvent.UserStartedSpeaking,
      RTVIEvent.UserStoppedSpeaking,
      RTVIEvent.UserTranscript,
      RTVIEvent.BotStartedSpeaking,
      RTVIEvent.BotStoppedSpeaking,
      RTVIEvent.BotOutput,
      RTVIEvent.BotTranscript,
      RTVIEvent.BotLlmStarted,
      RTVIEvent.BotLlmText,
      RTVIEvent.BotLlmStopped,
      RTVIEvent.LLMFunctionCall,
      RTVIEvent.BotTtsStarted,
      RTVIEvent.BotTtsText,
      RTVIEvent.BotTtsStopped,
      RTVIEvent.TrackStarted,
      RTVIEvent.TrackStopped,
      RTVIEvent.LocalAudioLevel,
      RTVIEvent.RemoteAudioLevel,
      RTVIEvent.DeviceError,
      RTVIEvent.ScreenShareError,
    ];

    const handlers = diagnosticEvents.map((eventName) => {
      const handler = createPipecatConsoleHandler(eventName);
      pipecatClient.on(eventName, handler);
      return { eventName, handler };
    });
    const enableBridge = () => setEventBridgeEnabled(true);
    const disableBridge = () => setEventBridgeEnabled(false);

    pipecatClient.on(RTVIEvent.BotConnected, enableBridge);
    pipecatClient.on(RTVIEvent.BotReady, enableBridge);
    pipecatClient.on(RTVIEvent.BotDisconnected, disableBridge);
    pipecatClient.on(RTVIEvent.Disconnected, disableBridge);

    console.info('[Pipecat] Browser console diagnostics enabled at DEBUG level');

    return () => {
      handlers.forEach(({ eventName, handler }) => {
        pipecatClient.off(eventName, handler);
      });
      pipecatClient.off(RTVIEvent.BotConnected, enableBridge);
      pipecatClient.off(RTVIEvent.BotReady, enableBridge);
      pipecatClient.off(RTVIEvent.BotDisconnected, disableBridge);
      pipecatClient.off(RTVIEvent.Disconnected, disableBridge);
    };
  }, [client]);

  useEffect(() => {
    const handleCustomEvent = (event: Event) => {
      const customEvent = event as CustomEvent<UiAction>;
      dispatchUiAction(customEvent.detail);
    };

    const handleMessage = (event: MessageEvent<unknown>) => {
      if (!event.data || typeof event.data !== 'object') return;
      const candidate = event.data as UiAction;
      if (candidate.type === 'ui_action' || typeof candidate.action === 'string') {
        dispatchUiAction(candidate);
      }
    };

    window.ClaimPilot = {
      dispatch: dispatchUiAction,
      runDemo: runEventDemo,
      reset: resetDemo,
      getState: () => ({ screen, claim }),
    };
    window.addEventListener('claimpilot:ui-action', handleCustomEvent);
    window.addEventListener('message', handleMessage);

    return () => {
      window.removeEventListener('claimpilot:ui-action', handleCustomEvent);
      window.removeEventListener('message', handleMessage);
    };
  }, [claim, dispatchUiAction, resetDemo, runEventDemo, screen]);

  useEffect(() => {
    if (!toast) return;

    const timer = window.setTimeout(() => setToast(''), 2600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    scrollActiveScreenToTop();
  }, [screen, scrollActiveScreenToTop]);

  useEffect(() => {
    return () => {
      clearTimers();
      objectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
      voiceAgentClientRef.current?.disconnect();
    };
  }, [clearTimers]);

  const evidenceStatusRows = useMemo(() => {
    const order: EvidenceStatus[] =
      claim.evidenceStatus === 'rejected'
        ? ['waiting', 'uploading', 'attached', 'analyzing', 'rejected']
        : ['waiting', 'uploading', 'attached', 'analyzing'];
    const labels: Record<EvidenceStatus, string> = {
      waiting: 'Waiting for photo',
      uploading: 'Uploading evidence',
      attached: 'Evidence attached',
      analyzing: 'Image analysis',
      analyzed: 'Analysis complete',
      rejected: 'Replacement needed',
    };
    const activeIndex = order.indexOf(claim.evidenceStatus);

    return order.map((status) => {
      const index = order.indexOf(status);
      const isDone = claim.evidenceStatus === 'analyzed' || (claim.evidenceStatus !== 'rejected' && index < activeIndex);
      const isActive = status === claim.evidenceStatus;
      return (
        <div className={`state-row ${isDone ? 'done' : ''} ${isActive ? 'active' : ''}`} key={status}>
          <span className="row">
            <span className="state-dot" />
            {labels[status]}
          </span>
          <span className="meta">{isDone ? 'Done' : isActive ? 'Now' : 'Pending'}</span>
        </div>
      );
    });
  }, [claim.evidenceStatus]);

  const uploadCopy = useMemo(() => {
    if (claim.evidenceStatus === 'uploading') return ['Uploading evidence', 'Securely attaching photo'];
    if (claim.evidenceStatus === 'attached') return ['Evidence attached', 'Ready for image analysis'];
    if (claim.evidenceStatus === 'analyzing') return ['Analyzing image', 'Checking visible damage and missing details'];
    if (claim.evidenceStatus === 'analyzed') return ['Analysis complete', 'Added to adjuster packet'];
    if (claim.evidenceStatus === 'rejected') return ['Upload replacement photo', 'The last photo did not match the claim'];
    return [`Tap to attach ${claim.requestedEvidence.toLowerCase() || 'requested evidence'}`, 'JPG or PNG from camera roll'];
  }, [claim.evidenceStatus, claim.requestedEvidence]);

  const claimProgressLabel =
    claim.status === 'New'
      ? 'No details captured yet'
      : `${completedCount} of ${FIELD_ORDER.length} claim details captured`;

  return (
    <main className="claimpilot-shell">
      <nav className="demo-controls" aria-label="Local demo event simulator" hidden>
        <button className="btn btn-primary" type="button" onClick={runEventDemo}>
          <Icon name="phone" /> Run event demo
        </button>
        <button className="btn btn-secondary" type="button" onClick={emitDetailsSample}>
          <Icon name="list" /> Details
        </button>
        <button className="btn btn-secondary" type="button" onClick={emitTimelineSample}>
          <Icon name="summary" /> Timeline
        </button>
        <button className="btn btn-secondary" type="button" onClick={emitSupervisorSample}>
          <Icon name="shield" /> Supervisor
        </button>
        <button className="btn btn-secondary" type="button" onClick={emitEvidenceSample}>
          <Icon name="aws" /> Evidence/AWS
        </button>
        <button className="btn btn-secondary" type="button" onClick={emitSubmitSample}>
          <Icon name="check" /> Submit
        </button>
        <button className="btn btn-ghost" type="button" onClick={runDemo}>
          Scripted fallback
        </button>
        <button className="btn btn-ghost" type="button" onClick={resetDemo}>
          Reset
        </button>
      </nav>

      <section className="app-stage" aria-label="ClaimPilot new claim submission application">
        <section className="panel customer-panel" aria-label="Customer mobile app">
          <div className="panel-head">
            <div className="panel-title">
              <span className="icon-box" aria-hidden="true"><Icon name="phone" /></span>
              <div>
                <h2>Customer mobile app</h2>
                <p>Customer-facing Northstar Insurance flow</p>
              </div>
            </div>
            <span className="status-pill success">iOS demo</span>
          </div>
          <div className="phone-wrap">
        <div className="phone" aria-label="ClaimPilot mobile insurance app">
          <div className="phone-screen">
            <div className={`mobile-app ${appPhase === 'app' ? '' : `phone-mode phone-mode-${appPhase}`}`}>
              <div className="statusbar" aria-label="iOS status">
                <span className="num">9:41</span>
                <div className="status-icons" aria-hidden="true">
                  <div className="signal"><span /><span /><span /></div>
                  <span>5G</span>
                  <div className="battery" />
                </div>
              </div>

              {appPhase === 'app' ? (
                <>
              <header className="app-header">
                <div className="row-between">
                  <div className="brand-lockup">
                    <div className="brand-mark" aria-hidden="true">
                      <Icon name="shield" />
                    </div>
                    <div>
                      <div className="brand-title">Northstar Insurance</div>
                      <div className="brand-subtitle">ClaimPilot intake</div>
                    </div>
                  </div>
                  <div className="call-state"><span className="pulse-dot" />{claim.status === 'New' ? 'Ready' : claim.status === 'Submitted' ? 'Submitted' : 'Listening'}</div>
                </div>
              </header>

              <div className="progress-wrap" aria-label="Claim submission progress">
                <div className="stepper">
                  {STEPS.map((step, index) => (
                    <div
                      className={`step ${index < activeStepIndex || activeStep === 'submit' && claim.status === 'Submitted' ? 'done' : ''} ${
                        index === activeStepIndex && claim.status !== 'Submitted' ? 'current' : ''
                      }`}
                      key={step}>
                      {step[0].toUpperCase() + step.slice(1)}
                    </div>
                  ))}
                </div>
              </div>

              <div className="screen-viewport" ref={screenViewportRef}>
                <section className={`app-screen ${screen === 'start' ? 'active' : ''}`} aria-label="Start a new claim">
                  <div className="screen-title">
                    <div>
                      <h1>Start a claim</h1>
                      <p className="screen-kicker">Policy {claim.policyId} · Comprehensive auto</p>
                    </div>
                    <span className="status-pill">New</span>
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>What do you need help with?</h2>
                      <span className="status-pill success">Covered</span>
                    </div>
                    <div className="choice-grid">
                      <button className={`choice ${claim.claimType === 'Auto accident' ? 'selected' : ''}`} type="button" onClick={startAutoClaim}>
                        <span className="choice-mark" aria-hidden="true"><Icon name="car" /></span>
                        <span><strong>Auto accident</strong><br />Collision, hit-and-run, or vehicle damage</span>
                        <span className={`status-pill ${claim.claimType === 'Auto accident' ? 'success' : ''}`}>
                          {claim.claimType === 'Auto accident' ? 'Selected' : 'Start'}
                        </span>
                      </button>
                      <button className="choice" type="button">
                        <span className="choice-mark" aria-hidden="true"><Icon name="glass" /></span>
                        <span><strong>Glass or windscreen</strong><br />Chips, cracks, or damaged mirrors</span>
                        <span />
                      </button>
                    </div>
                  </div>

                  <div className="module next-action">
                    <div className="action-icon" aria-hidden="true"><Icon name="chat" /></div>
                    <div>
                      <h2>Claim assistant</h2>
                      <p className="small-note">Start with your own words. The assistant will capture details as the conversation unfolds.</p>
                    </div>
                  </div>

                  <div className="bottom-actions single">
                    <button className="btn btn-primary full-width" type="button" onClick={startAutoClaim}>
                      Begin auto claim
                    </button>
                  </div>
                </section>

                <section className={`app-screen ${screen === 'details' ? 'active' : ''}`} aria-label="Claim details form">
                  <div className="screen-title">
                    <div>
                      <h1>Claim details</h1>
                      <p className="screen-kicker">Fields update from conversation</p>
                    </div>
                    <span className="status-pill success">Live</span>
                  </div>

                  <div className="module tint">
                    <div className="module-title">
                      <h2>What happened?</h2>
                      <span className={claim.whatHappenedSummary ? 'status-pill success' : 'status-pill warn'}>
                        {claim.whatHappenedSummary ? 'Pre-filled' : 'Listening'}
                      </span>
                    </div>
                    <p className="summary-copy">
                      {claim.whatHappenedSummary ||
                        'ClaimPilot will capture the first incident statement here as the customer speaks.'}
                    </p>
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>Safety check</h2>
                      <span className={claim.fields.injuryStatus.missing ? 'status-pill warn' : 'status-pill success'}>
                        {claim.fields.injuryStatus.missing ? 'Needed' : 'Complete'}
                      </span>
                    </div>
                    <div className="check-list">
                      <div className="check-item">
                        <span className={claim.fields.injuryStatus.missing ? 'status-pill warn' : 'status-pill success'}>
                          {claim.fields.injuryStatus.missing ? 'Needed' : 'Done'}
                        </span>
                        <strong>{displayValue(claim.fields.injuryStatus, 'Ask whether anyone is injured')}</strong>
                      </div>
                      {claim.safetySummary ? (
                        <div className="check-item">
                          <span className="status-pill success">Call</span>
                          {claim.safetySummary}
                        </div>
                      ) : null}
                    </div>
                  </div>

                  <div className="agent-chip" aria-live="polite">
                    <span>Assistant is listening for location, time, vehicle, damage, and police report details</span>
                    <span className="wave" aria-hidden="true"><i /><i /><i /></span>
                  </div>

                  <div className="field-grid">
                    {FIELD_ORDER.map((field) => {
                      const fieldState = claim.fields[field];
                      return (
                        <div
                          className={`field-card ${fieldState.missing ? 'missing' : ''} ${
                            fieldState.updated ? 'updated' : ''
                          } ${freshFields.has(field) ? 'is-fresh' : ''}`}
                          key={field}>
                          <span className="field-label">{FIELD_LABELS[field]}</span>
                          <span className={`status-pill ${fieldState.tone === 'success' ? 'success' : fieldState.tone === 'warn' ? 'warn' : ''}`}>
                            {fieldState.status}
                          </span>
                          <strong className="field-value">{displayValue(fieldState)}</strong>
                          {fieldState.note ? <span className="small-note">{fieldState.note}</span> : null}
                        </div>
                      );
                    })}
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>Timeline</h2>
                      <span className={`status-pill ${claim.timeline.length > 0 ? 'success' : 'warn'}`}>
                        {claim.timeline.length > 0 ? 'Structured' : 'Waiting'}
                      </span>
                    </div>
                    <TimelineList entries={claim.timeline} />
                  </div>

                  <button className="btn btn-secondary full-width" type="button" onClick={showCorrection}>
                    Replay vehicle correction
                  </button>

                  <div className="bottom-actions">
                    <button className="btn btn-secondary" type="button" onClick={() => navigate('start')}>Back</button>
                    <button className="btn btn-primary" type="button" onClick={() => navigate('people')}>Add people</button>
                  </div>
                </section>

                <section className={`app-screen ${screen === 'people' ? 'active' : ''}`} aria-label="People and vehicle details">
                  <div className="screen-title">
                    <div>
                      <h1>People involved</h1>
                      <p className="screen-kicker">Keep the packet adjuster-ready</p>
                    </div>
                    <span className="status-pill warn">{claim.missingInformation.length} needed</span>
                  </div>

                  <div className="form-stack">
                    <div className="input-row">
                      <label>Driver</label>
                      <div className="readonly-input">Policyholder</div>
                    </div>
                    <div className="input-row">
                      <label>Vehicle owner</label>
                      <div className="readonly-input">{claim.fields.vehicle.value.includes('spouse') ? 'Spouse' : 'Waiting for vehicle correction'}</div>
                    </div>
                    <div className="input-row">
                      <label>Other party</label>
                      <div className="readonly-input multiline">{displayValue(claim.fields.otherPartyStatus, 'Assistant has not captured other party details yet.')}</div>
                    </div>
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>Still needed</h2>
                      <span className="status-pill warn">Follow up</span>
                    </div>
                    <div className="check-list">
                      {claim.missingInformation.map((item) => (
                        <div className="check-item" key={item}><span className="status-pill warn">Needed</span>{item}</div>
                      ))}
                    </div>
                  </div>

                  <div className="bottom-actions evidence-actions">
                    <button className="btn btn-secondary" type="button" onClick={() => navigate('details')}>Back</button>
                    <button className="btn btn-primary" type="button" onClick={() => requestEvidence('Rear bumper photo')}>Add evidence</button>
                  </div>
                </section>

                <section className={`app-screen ${screen === 'evidence' ? 'active' : ''}`} aria-label="Evidence upload">
                  <div className="screen-title">
                    <div>
                      <h1>Evidence</h1>
                      <p className="screen-kicker">{claim.requestedEvidence || 'Photo'} requested</p>
                    </div>
                    <span className={`status-pill ${evidenceStatusTone(claim.evidenceStatus)}`}>
                      {EVIDENCE_STATUS_LABELS[claim.evidenceStatus]}
                    </span>
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>Requested evidence</h2>
                      <span className="status-pill">Assistant request</span>
                    </div>
                    <p className="summary-copy">The assistant asked for a clear rear bumper photo so the adjuster can review visible damage.</p>
                  </div>

                  <label
                    className={`upload-zone ${claim.evidenceStatus !== 'waiting' ? 'attached' : ''} ${claim.evidenceStatus === 'rejected' ? 'rejected' : ''}`}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={handleEvidenceDrop}>
                    <input type="file" accept="image/*" onChange={handleEvidenceFile} />
                    <span className="upload-visual" aria-hidden="true">
                      <span className="mini-icon"><Icon name={hasEvidencePreview ? 'camera' : 'upload'} /></span>
                    </span>
                    <span className="upload-title">{uploadCopy[0]}</span>
                    <span className="small-note">{claim.evidenceFileName || uploadCopy[1]}</span>
                    <span className="btn btn-secondary photo-button">Choose photo</span>
                  </label>

                  <div className="evidence-preview-panel" aria-label="Selected evidence preview">
                    <div className="evidence-preview">
                      {claim.selectedImageUrl ? (
                        <img src={claim.selectedImageUrl} alt="Selected vehicle damage evidence" />
                      ) : (
                        <div className="damage-illustration" aria-hidden="true" />
                      )}
                      <span className="preview-label">{claim.evidenceFileName || 'No photo selected'}</span>
                    </div>
                    <div className="evidence-preview-copy">
                      <div className="row-between">
                        <strong>{claim.evidenceFileName || claim.requestedEvidence || 'Requested photo'}</strong>
                        <span className={`status-pill ${evidenceStatusTone(claim.evidenceStatus)}`}>
                          {EVIDENCE_STATUS_LABELS[claim.evidenceStatus]}
                        </span>
                      </div>
                      <p className="small-note">
                        {claim.evidenceStatus === 'waiting'
                          ? 'Attach a clear, well-lit image before the assistant starts analysis.'
                          : claim.evidenceStatus === 'rejected'
                            ? 'The photo is attached but does not match the requested damage.'
                            : 'The photo is linked to this draft claim and ready for adjuster review.'}
                      </p>
                      {claim.evidenceStorage || claim.evidenceS3Key ? (
                        <p className="small-note">
                          Storage: {claim.evidenceStorage || claim.evidenceS3Key}
                        </p>
                      ) : null}
                      <div className="upload-progress" aria-label={`Evidence upload ${uploadProgress}% complete`}>
                        <span style={{ width: `${uploadProgress}%` }} />
                      </div>
                    </div>
                  </div>

                  <div className="module tight">
                    <div className="upload-state-list" aria-label="Upload states">
                      {evidenceStatusRows}
                    </div>
                  </div>

                  {claim.evidenceStatus === 'rejected' ? (
                    <div className="module evidence-verdict reject">
                      <div className="module-title">
                        <h2>Photo check</h2>
                        <span className="status-pill danger">Wrong photo</span>
                      </div>
                      <p className="summary-copy">{claim.evidenceResult}</p>
                      <p className="small-note">Upload the damaged bumper photo to continue the claim review.</p>
                    </div>
                  ) : null}

                  <div className="bottom-actions">
                    <button className="btn btn-secondary" type="button" onClick={() => navigate('people')}>Back</button>
                    <button
                      className="btn btn-secondary"
                      type="button"
                      onClick={() => simulateEvidenceUpload('unrelated-receipt-demo.jpg', undefined, undefined, { evidenceMismatch: true })}>
                      Attach unrelated
                    </button>
                    <button className="btn btn-primary" type="button" onClick={() => simulateEvidenceUpload()}>Attach sample</button>
                  </div>
                </section>

                <section className={`app-screen ${screen === 'review' ? 'active' : ''}`} aria-label="Review claim packet">
                  <div className="screen-title">
                    <div>
                      <h1>Review packet</h1>
                      <p className="screen-kicker">Ready to submit with open follow-ups</p>
                    </div>
                    <span className="status-pill warn">Review</span>
                  </div>

                  <div className="module tint">
                    <div className="module-title">
                      <h2>Pre-submit claim summary</h2>
                      <span className={claim.preSubmitSummary || claim.customerSummary ? 'status-pill success' : 'status-pill warn'}>
                        {claim.preSubmitSummary || claim.customerSummary ? 'Generated' : 'Waiting'}
                      </span>
                    </div>
                    <p className="summary-copy">
                      {claim.preSubmitSummary || claim.customerSummary || 'Claim summary will be generated before submission.'}
                    </p>
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>Timeline of events</h2>
                      <span className={`status-pill ${claim.timeline.length > 0 ? 'success' : 'warn'}`}>
                        {claim.timeline.length} events
                      </span>
                    </div>
                    <TimelineList entries={claim.timeline} />
                  </div>

                  <div className="damage-preview" role="img" aria-label="Rear bumper photo preview">
                    {claim.selectedImageUrl ? <img src={claim.selectedImageUrl} alt="Uploaded vehicle damage" /> : <div className="damage-illustration" />}
                    <span className="preview-label">{claim.evidenceFileName || 'Rear bumper photo'}</span>
                    {claim.evidenceStorage || claim.evidenceS3Key ? (
                      <span className="preview-label">{claim.evidenceStorage || claim.evidenceS3Key}</span>
                    ) : null}
                  </div>

                  <div className={`module evidence-verdict ${claim.evidenceStatus === 'rejected' ? 'reject' : 'pass'}`}>
                    <div className="module-title">
                      <h2>Photo check</h2>
                      <span className={`status-pill ${claim.evidenceStatus === 'rejected' ? 'danger' : 'success'}`}>
                        {claim.evidenceStatus === 'rejected' ? 'Needs replacement' : 'Matches claim'}
                      </span>
                    </div>
                    <p className="summary-copy">
                      {claim.evidenceResult || 'Waiting for image analysis.'}
                    </p>
                    {claim.evidenceAnalysisProvider ? (
                      <p className="small-note">Source: {claim.evidenceAnalysisProvider}</p>
                    ) : null}
                    <div className="check-list compact">
                      <div className="check-item">
                        <span className={`status-pill ${claim.evidenceStatus === 'rejected' ? 'danger' : 'success'}`}>
                          {claim.evidenceStatus === 'rejected' ? 'Replace' : 'Done'}
                        </span>
                        {claim.evidenceStatus === 'rejected'
                          ? 'Photo does not match the requested vehicle damage'
                          : 'Photo attached to adjuster packet'}
                      </div>
                    </div>
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>Adjuster summary</h2>
                      <span className="status-pill success">Generated</span>
                    </div>
                    <p className="summary-copy">{claim.adjusterSummary}</p>
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>Packet checklist</h2>
                      <span className="status-pill warn">{claim.missingInformation.length} open</span>
                    </div>
                    <div className="check-list">
                      <div className="check-item"><span className="status-pill success">Done</span><strong>Incident details captured</strong></div>
                      <div className="check-item"><span className="status-pill success">Done</span><strong>{claim.requestedEvidence || 'Evidence'} attached</strong></div>
                      {claim.missingInformation.map((item) => (
                        <div className="check-item" key={item}><span className="status-pill warn">Needed</span>{item}</div>
                      ))}
                    </div>
                  </div>

                  <div className="bottom-actions">
                    <button className="btn btn-secondary" type="button" onClick={() => navigate('evidence')}>Back</button>
                    <button className="btn btn-primary" type="button" onClick={() => showFinalPacket(claim.claimId)}>Submit claim</button>
                  </div>
                </section>

                <section className={`app-screen ${screen === 'submitted' ? 'active' : ''}`} aria-label="Claim submitted">
                  <div className="screen-title">
                    <div>
                      <h1>Claim submitted</h1>
                      <p className="screen-kicker">Auto physical damage review</p>
                    </div>
                    <span className="status-pill success">Submitted</span>
                  </div>

                  <div className="module">
                    <div className="row-between">
                      <div>
                        <span className="meta">Claim ID</span>
                        <h2 className="claim-id">{claim.claimId}</h2>
                      </div>
                      <span className="status-pill success">Confirmed</span>
                    </div>
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>Recommended next action</h2>
                      <span className="status-pill warn">Follow up</span>
                    </div>
                    <div className="check-list">
                      {claim.nextActions.map((item, index) => (
                        <div className="check-item" key={item}>
                          <span className={`status-pill ${index === 0 ? 'success' : 'warn'}`}>{index === 0 ? 'Done' : 'Needed'}</span>
                          {item}
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>Customer SMS</h2>
                      <span className={`status-pill ${claimSmsSent ? 'success' : ''}`}>
                        {claimSmsSent ? 'Sent' : 'Queued'}
                      </span>
                    </div>
                    <p className="summary-copy">
                      {claimSmsText ||
                        claim.customerFollowUp ||
                        `Claim ${claim.claimId} submitted. We received your evidence. A claims specialist will review the packet and contact you if more information is needed.`}
                    </p>
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>Final packet</h2>
                      <span className="status-pill success">Created</span>
                    </div>
                    <p className="summary-copy">{claim.adjusterSummary}</p>
                    {claim.finalPacketStorage ? <p className="small-note">Packet: {claim.finalPacketStorage}</p> : null}
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>Timeline of events</h2>
                      <span className="status-pill success">{claim.timeline.length} events</span>
                    </div>
                    <TimelineList entries={claim.timeline} />
                  </div>

                  <div className="module">
                    <div className="module-title">
                      <h2>Fault and excess</h2>
                      <span className={`status-pill ${claim.faultDecision.available ? 'success' : 'warn'}`}>
                        {claim.faultDecision.available ? 'Estimated' : 'Manual review'}
                      </span>
                    </div>
                    {claim.faultDecision.available ? (
                      <div className="field-grid">
                        <div className="field-card">
                          <span className="field-label">Fault decision</span>
                          <span className="status-pill success">Provisional</span>
                          <strong className="field-value">{claim.faultDecision.faultDecision}</strong>
                        </div>
                        <div className="field-card">
                          <span className="field-label">Excess estimate</span>
                          <span className="status-pill success">Policy estimate</span>
                          <strong className="field-value">{claim.faultDecision.excessEstimate}</strong>
                        </div>
                        {claim.faultDecision.explanation ? <p className="small-note">{claim.faultDecision.explanation}</p> : null}
                      </div>
                    ) : (
                      <p className="summary-copy">
                        {claim.faultDecision.reason ||
                          'Fault and excess estimate unavailable until supervisor review is complete.'}
                      </p>
                    )}
                  </div>

                  <div className="bottom-actions single">
                    <button className="btn btn-secondary full-width" type="button" onClick={resetDemo}>Start another claim</button>
                  </div>
                </section>

                {toast ? (
                  <div className="toast" role="status" aria-live="polite">
                    <span className="mini-icon" aria-hidden="true"><Icon name="check" /></span>
                    <span>{toast}</span>
                  </div>
                ) : null}
              </div>

              <nav className="bottom-nav" aria-label="App navigation">
                {NAV_ITEMS.map((item) => (
                  <button
                    className={`nav-button ${item.screen === screen || STEP_BY_SCREEN[item.screen] === STEP_BY_SCREEN[screen] && item.screen !== 'start' ? 'active' : ''}`}
                    key={item.screen}
                    type="button"
                    onClick={() => navigate(item.screen)}>
                    <span className="nav-icon" aria-hidden="true"><Icon name={item.icon} /></span>
                    {item.label}
                  </button>
                ))}
              </nav>
                </>
              ) : (
                <div className="phone-entry-screen">
                  {appPhase === 'dialer' ? (
                    <section className="call-screen" aria-label="Phone dialer">
                      <div className="call-header">
                        <span className="mini-icon" aria-hidden="true"><Icon name="phone" /></span>
                        <div>
                          <h1>Phone</h1>
                          <p className="screen-kicker">Northstar claims line</p>
                        </div>
                      </div>

                      <div className="dial-display" aria-label="Dialed claims number">
                        {dialedNumber || CLAIMS_PHONE_NUMBER}
                      </div>

                      <div className="dial-pad" aria-label="Dial pad">
                        {'123456789*0#'.split('').map((digit) => (
                          <button
                            className="dial-key"
                            key={digit}
                            type="button"
                            onClick={() => appendDialDigit(digit)}>
                            {digit}
                          </button>
                        ))}
                      </div>

                      <div className="call-actions">
                        <button className="btn btn-secondary" type="button" onClick={() => setDialedNumber(CLAIMS_PHONE_NUMBER)}>
                          Claims number
                        </button>
                        <button className="btn btn-secondary icon-only" type="button" onClick={deleteDialDigit} aria-label="Delete digit">
                          <Icon name="alert" />
                        </button>
                      </div>

                      <button className="call-button" type="button" onClick={startPhoneCallWithDaily} aria-label="Call claims">
                        <Icon name="phone" />
                      </button>
                    </section>
                  ) : null}

                  {appPhase === 'connecting' ? (
                    <section className="call-screen connecting-call-screen" aria-label="Connecting ClaimPilot call">
                      <div className="active-call-top">
                        <span className="status-pill"><span className="pulse-dot" />Calling</span>
                        <p className="meta">{dialedNumber || CLAIMS_PHONE_NUMBER}</p>
                      </div>
                      <div className="caller-avatar connecting-avatar" aria-hidden="true">
                        <Icon name="phone" />
                      </div>
                      <div className="caller-copy">
                        <h1>Northstar Claims</h1>
                        <p>Connecting to ClaimPilot</p>
                      </div>
                      <div className="call-connecting-card" aria-live="polite">
                        <strong>Starting secure voice session</strong>
                        <div className="connecting-dots" aria-hidden="true">
                          <span />
                          <span />
                          <span />
                        </div>
                      </div>
                      <div className="call-control-row" aria-label="Call controls">
                        <span><Icon name="mic" />Mute</span>
                        <span><Icon name="chat" />Keypad</span>
                        <span><Icon name="phone" />Speaker</span>
                      </div>
                    </section>
                  ) : null}

                  {appPhase === 'call' ? (
                    <section className="call-screen active-call-screen" aria-label="Active ClaimPilot call">
                      {showSmsNotification ? (
                        <button className="sms-notification-banner" type="button" onClick={() => sendSecureLink()}>
                          <span className="sms-notification-icon" aria-hidden="true"><Icon name="chat" /></span>
                          <span>
                            <strong>MESSAGES</strong>
                            <span>Northstar Insurance</span>
                            <small>Your secure claim link is ready.</small>
                          </span>
                        </button>
                      ) : null}
                      <div className="active-call-top">
                        <span className="status-pill success"><span className="pulse-dot" />Connected</span>
                        <p className="meta">{dialedNumber || CLAIMS_PHONE_NUMBER}</p>
                      </div>
                      <div className="caller-avatar" aria-hidden="true"><Icon name="shield" /></div>
                      <div className="caller-copy">
                        <h1>ClaimPilot</h1>
                        <p>Northstar Insurance claims assistant</p>
                      </div>
                      <div className="call-transcript-card">
                        <strong>Assistant</strong>
                        <p>{CLAIMPILOT_PHONE_PROMPT}</p>
                      </div>
                      <div className="call-control-row" aria-label="Call controls">
                        <span><Icon name="mic" />Mute</span>
                        <span><Icon name="chat" />Keypad</span>
                        <span><Icon name="phone" />Speaker</span>
                      </div>
                    </section>
                  ) : null}

                  {appPhase === 'secureLink' ? (
                    <section className="call-screen message-screen" aria-label="Secure claim link">
                      <div className="call-header">
                        <span className="mini-icon" aria-hidden="true"><Icon name="chat" /></span>
                        <div>
                          <h1>Messages</h1>
                          <p className="screen-kicker">Northstar Insurance</p>
                        </div>
                      </div>
                      <div className="message-thread">
                        <div className="message-bubble received">
                          Your secure Northstar claim link is ready. Continue in the app to verify your session and start claim {claim.claimId}.
                        </div>
                        <button className="secure-link-card" type="button" onClick={completeSecureHandoff}>
                          <span className="mini-icon" aria-hidden="true"><Icon name="shield" /></span>
                          <span>
                            <strong>Continue securely</strong>
                            <small>{secureClaimLink(claim.claimId)}</small>
                          </span>
                        </button>
                      </div>
                    </section>
                  ) : null}

                  {appPhase === 'claimSms' ? (
                    <section className="call-screen message-screen" aria-label="Submitted claim SMS">
                      <div className="call-header">
                        <span className="mini-icon" aria-hidden="true"><Icon name="chat" /></span>
                        <div>
                          <h1>Messages</h1>
                          <p className="screen-kicker">Northstar Insurance</p>
                        </div>
                      </div>
                      <div className="message-thread">
                        <div className="message-bubble received compact">
                          Your secure Northstar claim link is ready. Continue in the app to verify your session and start claim {claim.claimId}.
                        </div>
                        <div className="message-bubble received claim-summary-sms">
                          <strong>Claim submitted</strong>
                          <span>{claimSmsText || claim.customerFollowUp || `Claim ${claim.claimId} submitted. We received your claim details and evidence.`}</span>
                          <small>Reference {claim.claimId}</small>
                        </div>
                        <button className="secure-link-card message-summary-card" type="button" onClick={() => setAppPhase('app')}>
                          <span className="mini-icon" aria-hidden="true"><Icon name="shield" /></span>
                          <span>
                            <strong>View claim packet</strong>
                            <small>{claim.finalPacketStorage || `Northstar claim ${claim.claimId}`}</small>
                          </span>
                        </button>
                      </div>
                    </section>
                  ) : null}

                  {appPhase === 'verified' ? (
                    <section className="call-screen verified-screen" aria-label="Verified Northstar session">
                      <div className="verified-mark" aria-hidden="true"><Icon name="check" /></div>
                      <h1>Verified</h1>
                      <p>Opening your authenticated Northstar claim session.</p>
                      <div className="session-progress" aria-hidden="true"><span /></div>
                    </section>
                  ) : null}
                </div>
              )}
            </div>
          </div>
        </div>
          </div>
        </section>

        <aside className="panel supervisor-panel" aria-label="Claims Supervisor">
          <div className="panel-head">
            <div className="panel-title">
              <span className="icon-box" aria-hidden="true"><Icon name="list" /></span>
              <div>
                <h2>Claims Supervisor</h2>
                <p>Internal workflow, triage risk, and claim readiness</p>
              </div>
            </div>
            <span className="status-pill">Operations</span>
          </div>

          <div className="panel-body">
            <div className="supervisor-workflow" aria-label="Supervisor workflow checklist">
              {supervisor.workflow.map((item) => (
                <div className={`workflow-row ${item.status.toLowerCase()}`} key={item.step}>
                  <span className={`icon-box ${item.status === 'Complete' ? 'green' : item.status === 'Missing' || item.status === 'Blocked' ? 'amber' : ''}`} aria-hidden="true">
                    <Icon name={item.status === 'Complete' ? 'check' : item.status === 'Missing' || item.status === 'Blocked' ? 'alert' : 'list'} />
                  </span>
                  <div>
                    <strong>{item.step}</strong>
                    <p>{item.detail}</p>
                  </div>
                  <span className={`status-pill ${statusTone(item.status)}`}>{item.status}</span>
                </div>
              ))}
            </div>

            <div className="risk-card">
              <div className="row-between">
                <div>
                  <h2>Claim triage risk</h2>
                  <p className="small-note">Risk level: {supervisor.triageRiskLevel}</p>
                </div>
                <strong className="risk-number">{supervisor.triageRiskPercent}%</strong>
              </div>
              <div className="risk-meter" aria-label={`Claim triage risk ${supervisor.triageRiskPercent} percent`}>
                <span style={{ width: `${supervisor.triageRiskPercent}%` }} />
              </div>
              <div className="signal-list">
                {supervisor.triageSignals.map((signal) => (
                  <div className="signal-row" key={signal}>
                    <span className="icon-box green" aria-hidden="true"><Icon name="check" /></span>
                    <span>{signal}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="module">
              <div className="module-title">
                <h2>Supervisor recommendation</h2>
                <span className={`status-pill ${riskTone}`}>{supervisor.triageRiskLevel}</span>
              </div>
              <p className="summary-copy">{supervisor.recommendation}</p>
              <p className="small-note">Previous claim history: {supervisor.previousClaimHistory}</p>
              {supervisor.blockers.length > 0 ? (
                <div className="check-list compact">
                  {supervisor.blockers.map((blocker) => (
                    <div className="check-item" key={blocker}>
                      <span className="status-pill warn">Open</span>
                      {blocker}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="metric-grid">
              <div className="metric"><b>4 min</b><span>Guided intake target</span></div>
              <div className="metric"><b>{claim.evidenceStatus === 'waiting' ? 0 : 1}</b><span>Evidence item received</span></div>
              <div className="metric"><b>{supervisor.blockers.length}</b><span>Open blockers</span></div>
            </div>

            <div className="session-card">
              <div className="detail-row"><span>Policy</span><strong>{claim.policyId}</strong></div>
              <div className="detail-row"><span>Claim</span><strong>{claim.claimId}</strong></div>
              <div className="detail-row"><span>Status</span><strong>{claim.status}</strong></div>
              <p className="small-note">{claimProgressLabel}</p>
            </div>
          </div>
        </aside>

        <aside className="panel rail-panel" aria-label="Live voice event rail">
          <div className="panel-head">
            <div className="panel-title">
              <span className="icon-box" aria-hidden="true"><Icon name="list" /></span>
              <div>
                <h2>Live event rail</h2>
                <p>Voice runtime events, Deepgram transcript, and UI event stream</p>
              </div>
            </div>
            <span className={`status-pill ${voiceRuntime === 'voice_agent' ? 'teal' : claim.status === 'Submitted' ? 'success' : ''}`}>
              {voiceSourceLabel}
            </span>
          </div>

          <div className="panel-body rail-body">
            <div className="rail-section">
              <h3><Icon name="list" />Voice events</h3>
              <div className="tool-event-list" aria-label="Voice runtime events">
                {toolEvents.map((event) => (
                  <article className="tool-event-row" key={event.id}>
                    <span className="meta num">{event.time}</span>
                    <div>
                      <strong>{event.tool}</strong>
                      <p>{event.detail}</p>
                      {event.payloadPreview ? <code>{event.payloadPreview}</code> : null}
                    </div>
                    <span className={`status-pill ${statusTone(event.status)}`}>{event.status}</span>
                  </article>
                ))}
              </div>
            </div>

            <div className="rail-section">
              <h3><Icon name="mic" />Deepgram transcript</h3>
              {voiceRuntime === 'pipecat' ? (
                <div className="transcript" aria-label="Live transcript">
                  <PipecatTranscriptList />
                </div>
              ) : (
                <div className="transcript" aria-label="Live transcript">
                  {transcript.map((line) => (
                    <p key={line.id}><strong>{line.speaker}:</strong> {line.text}</p>
                  ))}
                </div>
              )}
            </div>

            <div className="rail-section">
              <h3><Icon name="summary" />UI event bridge</h3>
              <div className="event-log" aria-label="Live agent events">
                {events.map((event) => (
                  <article className="event-row" key={event.id}>
                    <span className="meta num">{event.time}</span>
                    <div><p>{event.title}</p><span>{event.detail}</span></div>
                  </article>
                ))}
              </div>
            </div>

            {voiceRuntime === 'pipecat' ? (
              <details className="voice-diagnostics">
                <summary>Voice diagnostics</summary>
                <div className="diagnostics-grid">
                  <EventsPanel />
                </div>
              </details>
            ) : (
              <div className="voice-card compact">
                <div className="module-title">
                  <h2>Voice Agent diagnostics</h2>
                  <span className={`status-pill ${voiceAgentStatus === 'connected' || voiceAgentStatus === 'listening' ? 'success' : voiceAgentStatus === 'error' ? 'danger' : ''}`}>
                    {voiceAgentStatus}
                  </span>
                </div>
                <div className="voice-agent-metrics">
                  <div className="detail-row"><span>Relay</span><strong>{voiceAgentDetail}</strong></div>
                  <div className="detail-row">
                    <span>Latest latency</span>
                    <strong>{voiceAgentLatency ? `${voiceAgentLatency.ms} ms` : 'Waiting'}</strong>
                  </div>
                  {voiceAgentLatency?.tttMs ? (
                    <div className="detail-row"><span>Think time</span><strong>{voiceAgentLatency.tttMs} ms</strong></div>
                  ) : null}
                  {voiceAgentLatency?.ttsMs ? (
                    <div className="detail-row"><span>Speak time</span><strong>{voiceAgentLatency.ttsMs} ms</strong></div>
                  ) : null}
                </div>
              </div>
            )}

            <details className="admin-voice-controls">
              <summary><Icon name="mic" />Admin voice controls</summary>
              <div className="voice-card compact">
                <div className="module-title">
                  <h2>Voice runtime</h2>
                  <span className={`status-pill ${voiceRuntime === 'voice_agent' ? 'teal' : 'success'}`}>
                    {voiceSourceLabel}
                  </span>
                </div>
                <div className="runtime-switch" role="group" aria-label="Voice runtime">
                  <button
                    className={`runtime-option ${voiceRuntime === 'pipecat' ? 'active' : ''}`}
                    type="button"
                    onClick={() => setVoiceRuntime('pipecat')}
                  >
                    Pipecat cascade
                  </button>
                  <button
                    className={`runtime-option ${voiceRuntime === 'voice_agent' ? 'active' : ''}`}
                    type="button"
                    onClick={() => setVoiceRuntime('voice_agent')}
                  >
                    Deepgram Voice Agent
                  </button>
                </div>
                {voiceRuntime === 'pipecat' ? (
                  <div className="voice-controls">
                    {showTransportSelector ? (
                      <TransportSelect
                        transportType={transportType}
                        onTransportChange={onTransportChange}
                        availableTransports={availableTransports}
                      />
                    ) : (
                      <span className="status-pill">{TRANSPORT_LABELS[transportType]} transport</span>
                    )}
                    <UserAudioControl size="lg" />
                    <ConnectButton
                      size="lg"
                      onConnect={connectPipecat}
                      onDisconnect={handleDisconnect}
                    />
                  </div>
                ) : (
                  <div className="voice-agent-controls">
                    <div className="detail-row"><span>Status</span><strong>{voiceAgentDetail}</strong></div>
                    <div className="voice-controls">
                      <button
                        className="btn btn-primary"
                        type="button"
                        onClick={() => void connectVoiceAgent()}
                        disabled={voiceAgentStatus === 'connecting' || voiceAgentStatus === 'connected' || voiceAgentStatus === 'listening'}
                      >
                        Connect
                      </button>
                      <button
                        className="btn btn-secondary"
                        type="button"
                        onClick={toggleVoiceAgentMuted}
                        disabled={!voiceAgentClientRef.current?.isConnected}
                      >
                        {voiceAgentMuted ? 'Unmute' : 'Mute'}
                      </button>
                      <button className="btn btn-ghost" type="button" onClick={disconnectVoiceAgent}>
                        Disconnect
                      </button>
                    </div>
                    <div className="latency-badges" aria-label="Voice Agent latency">
                      <span className="status-pill">{voiceAgentLatency?.metric ?? 'Latency pending'}</span>
                      <span className="status-pill teal">{voiceAgentLatency ? `${voiceAgentLatency.ms} ms` : 'No turn yet'}</span>
                    </div>
                  </div>
                )}
              </div>
            </details>
          </div>
        </aside>
      </section>
    </main>
  );
};
