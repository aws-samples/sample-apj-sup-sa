export type VoiceAgentStatus =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'listening'
  | 'muted'
  | 'disconnected'
  | 'agent_audio_done'
  | 'warning'
  | 'error';

export interface VoiceAgentTranscriptEvent {
  speaker: 'Customer' | 'Assistant' | 'System';
  text: string;
}

export interface VoiceAgentLatencyEvent {
  metric: string;
  ms: number;
  tttMs?: number;
  ttsMs?: number;
}

export interface VoiceAgentUiAction {
  type?: string;
  action: string;
  [key: string]: unknown;
}

interface VoiceAgentClientOptions {
  url: string;
  onStatus?: (status: VoiceAgentStatus, detail?: string) => void;
  onTranscript?: (event: VoiceAgentTranscriptEvent) => void;
  onUiAction?: (action: VoiceAgentUiAction) => void;
  onLatency?: (event: VoiceAgentLatencyEvent) => void;
  onEvent?: (title: string, detail: string) => void;
}

function floatToPcm16(samples: Float32Array) {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);

  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }

  return buffer;
}

function downsample(input: Float32Array, inputRate: number, outputRate: number) {
  if (inputRate === outputRate) return input;

  const ratio = inputRate / outputRate;
  const outputLength = Math.max(1, Math.round(input.length / ratio));
  const output = new Float32Array(outputLength);

  for (let outputIndex = 0; outputIndex < outputLength; outputIndex += 1) {
    const start = Math.floor(outputIndex * ratio);
    const end = Math.min(Math.floor((outputIndex + 1) * ratio), input.length);
    let sum = 0;
    let count = 0;

    for (let inputIndex = start; inputIndex < end; inputIndex += 1) {
      sum += input[inputIndex];
      count += 1;
    }

    output[outputIndex] = count > 0 ? sum / count : input[start] ?? 0;
  }

  return output;
}

function pcm16ToFloat32(buffer: ArrayBuffer) {
  const view = new DataView(buffer);
  const samples = new Float32Array(buffer.byteLength / 2);

  for (let index = 0; index < samples.length; index += 1) {
    samples[index] = view.getInt16(index * 2, true) / 0x8000;
  }

  return samples;
}

export class VoiceAgentClient {
  private url: string;
  private socket: WebSocket | null = null;
  private options: VoiceAgentClientOptions;
  private audioContext: AudioContext | null = null;
  private mediaStream: MediaStream | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private processorNode: ScriptProcessorNode | null = null;
  private nextPlaybackTime = 0;
  private muted = false;
  private connected = false;
  private inputSampleRate = 16000;
  private outputSampleRate = 24000;

  constructor(options: VoiceAgentClientOptions) {
    this.url = options.url;
    this.options = options;
  }

  get isConnected() {
    return this.connected;
  }

  get isMuted() {
    return this.muted;
  }

  async connect() {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) return;

    this.options.onStatus?.('connecting', 'Connecting to Deepgram Voice Agent relay');
    this.audioContext = new AudioContext();
    await this.audioContext.resume();

    await this.startMicrophone();

    this.socket = new WebSocket(this.url);
    this.socket.binaryType = 'arraybuffer';

    await new Promise<void>((resolve, reject) => {
      if (!this.socket) {
        reject(new Error('Voice Agent socket was not created'));
        return;
      }

      this.socket.addEventListener(
        'open',
        () => {
          this.connected = true;
          this.options.onStatus?.('connected', 'Voice Agent relay connected');
          resolve();
        },
        { once: true }
      );

      this.socket.addEventListener(
        'error',
        () => {
          reject(new Error('Voice Agent relay connection failed'));
        },
        { once: true }
      );
    });

    this.socket.addEventListener('message', (event) => {
      if (event.data instanceof ArrayBuffer) {
        this.playPcmAudio(event.data);
        return;
      }

      if (event.data instanceof Blob) {
        event.data.arrayBuffer().then((buffer) => this.playPcmAudio(buffer));
        return;
      }

      this.handleJsonMessage(String(event.data));
    });

    this.socket.addEventListener('close', () => {
      this.connected = false;
      this.options.onStatus?.('disconnected', 'Voice Agent relay disconnected');
      this.stopAudio();
    });
  }

  disconnect() {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: 'stop' }));
    }
    this.socket?.close();
    this.socket = null;
    this.connected = false;
    this.stopAudio();
    this.options.onStatus?.('disconnected', 'Voice Agent disconnected');
  }

  setMuted(muted: boolean) {
    this.muted = muted;
    this.options.onStatus?.(muted ? 'muted' : 'listening', muted ? 'Microphone muted' : 'Listening');
  }

  sendAppEvent(action: string, payload: Record<string, unknown> = {}) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify({ type: 'app_event', action, payload, ...payload }));
    return true;
  }

  sendInjectedUserMessage(content: string) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify({ type: 'inject_user_message', content }));
    return true;
  }

  private async startMicrophone() {
    this.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    if (!this.audioContext) return;

    this.sourceNode = this.audioContext.createMediaStreamSource(this.mediaStream);
    this.processorNode = this.audioContext.createScriptProcessor(4096, 1, 1);

    this.processorNode.onaudioprocess = (event) => {
      const output = event.outputBuffer.getChannelData(0);
      output.fill(0);

      if (
        this.muted ||
        !this.socket ||
        this.socket.readyState !== WebSocket.OPEN ||
        !this.audioContext
      ) {
        return;
      }

      const input = event.inputBuffer.getChannelData(0);
      const downsampled = downsample(input, this.audioContext.sampleRate, this.inputSampleRate);
      this.socket.send(floatToPcm16(downsampled));
    };

    this.sourceNode.connect(this.processorNode);
    this.processorNode.connect(this.audioContext.destination);
    this.options.onStatus?.('listening', 'Microphone streaming to Voice Agent relay');
  }

  private stopAudio() {
    this.processorNode?.disconnect();
    this.sourceNode?.disconnect();
    this.mediaStream?.getTracks().forEach((track) => track.stop());
    this.processorNode = null;
    this.sourceNode = null;
    this.mediaStream = null;
    this.nextPlaybackTime = 0;
  }

  private playPcmAudio(buffer: ArrayBuffer) {
    if (!this.audioContext || buffer.byteLength === 0) return;

    const samples = pcm16ToFloat32(buffer);
    const audioBuffer = this.audioContext.createBuffer(
      1,
      samples.length,
      this.outputSampleRate
    );
    audioBuffer.copyToChannel(samples, 0);

    const source = this.audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(this.audioContext.destination);

    const startAt = Math.max(this.audioContext.currentTime + 0.02, this.nextPlaybackTime);
    source.start(startAt);
    this.nextPlaybackTime = startAt + audioBuffer.duration;
  }

  private handleJsonMessage(raw: string) {
    let message: Record<string, unknown>;

    try {
      message = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return;
    }

    if (message.type === 'status') {
      this.options.onStatus?.(
        (typeof message.status === 'string' ? message.status : 'connected') as VoiceAgentStatus,
        typeof message.detail === 'string' ? message.detail : undefined
      );
      return;
    }

    if (message.type === 'transcript') {
      this.options.onTranscript?.({
        speaker:
          message.speaker === 'Customer' || message.speaker === 'Assistant'
            ? message.speaker
            : 'System',
        text: typeof message.text === 'string' ? message.text : '',
      });
      return;
    }

    if (message.type === 'latency') {
      this.options.onLatency?.({
        metric: typeof message.metric === 'string' ? message.metric : 'voice_agent_latency',
        ms: typeof message.ms === 'number' ? message.ms : 0,
        tttMs: typeof message.tttMs === 'number' ? message.tttMs : undefined,
        ttsMs: typeof message.ttsMs === 'number' ? message.ttsMs : undefined,
      });
      return;
    }

    if (message.type === 'ui_actions' && Array.isArray(message.actions)) {
      message.actions.forEach((action) => {
        if (action && typeof action === 'object' && 'action' in action) {
          this.options.onUiAction?.(action as VoiceAgentUiAction);
        }
      });
      return;
    }

    if (message.type === 'ui_action' || typeof message.action === 'string') {
      this.options.onUiAction?.(message as VoiceAgentUiAction);
      return;
    }

    if (message.type === 'voice_agent_event') {
      this.options.onEvent?.('Voice Agent event', JSON.stringify(message.event ?? message));
    }
  }
}
