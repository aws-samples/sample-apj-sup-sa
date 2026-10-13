export interface AudioStreamConfig {
  sampleRate: 16000;
  channelCount: 1;
  encoding: 'pcm';
  bitsPerSample: 16;
}

export const AUDIO_CONFIG: AudioStreamConfig = {
  sampleRate: 16000,
  channelCount: 1,
  encoding: 'pcm',
  bitsPerSample: 16,
};

export interface AudioChunk {
  data: string;
  timestamp: number;
  sequenceNumber: number;
}
