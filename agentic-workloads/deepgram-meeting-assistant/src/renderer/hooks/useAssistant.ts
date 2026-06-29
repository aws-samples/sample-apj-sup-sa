/**
 * useAssistant
 *
 * 음성 어시스턴트(wake word → LLM → TTS) 이벤트를 구독하고:
 *  - assistant_text: LLM 응답 텍스트를 누적해 화면 표시용 상태로 노출
 *  - assistant_audio: base64 PCM(s16le) 청크를 AudioContext로 끊김 없이 재생
 *  - assistant_start/end: 진행 상태(query, isResponding) 관리
 *
 * 오디오는 chunk가 도착하는 대로 AudioContext의 타임라인에 이어붙여 스케줄링한다
 * (각 buffer를 직전 buffer 끝나는 시점에 start). 이렇게 하면 네트워크로 조각조각
 * 오는 PCM도 연속 재생된다.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { AssistantEvent } from '../../shared/types/pipecat-protocol';

export interface AssistantState {
  isResponding: boolean;
  query: string | null;
  responseText: string;
}

const INITIAL: AssistantState = {
  isResponding: false,
  query: null,
  responseText: '',
};

function base64ToInt16Array(b64: string): Int16Array {
  const binary = atob(b64);
  const len = binary.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  // s16le PCM. 길이가 홀수면 마지막 바이트는 버린다(불완전 샘플 방지).
  return new Int16Array(bytes.buffer, 0, Math.floor(len / 2));
}

export function useAssistant() {
  const [state, setState] = useState<AssistantState>(INITIAL);

  const audioCtxRef = useRef<AudioContext | null>(null);
  // 다음 buffer를 시작할 AudioContext 시각. 끊김 없는 연속 재생을 위한 커서.
  const nextStartTimeRef = useRef<number>(0);

  const ensureAudioContext = useCallback((sampleRate: number): AudioContext => {
    if (!audioCtxRef.current || audioCtxRef.current.sampleRate !== sampleRate) {
      // 샘플레이트가 바뀌면(또는 최초) 새 컨텍스트를 만든다.
      audioCtxRef.current?.close().catch(() => {});
      audioCtxRef.current = new AudioContext({ sampleRate });
      nextStartTimeRef.current = 0;
    }
    return audioCtxRef.current;
  }, []);

  const playChunk = useCallback(
    (data: string, sampleRate: number) => {
      const ctx = ensureAudioContext(sampleRate);
      if (ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }

      const pcm = base64ToInt16Array(data);
      if (pcm.length === 0) return;

      const buffer = ctx.createBuffer(1, pcm.length, sampleRate);
      const channel = buffer.getChannelData(0);
      for (let i = 0; i < pcm.length; i++) {
        channel[i] = pcm[i] / 32768;
      }

      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);

      // 이미 예약된 재생 끝(또는 현재 시각) 이후에 이어붙인다.
      const startAt = Math.max(ctx.currentTime, nextStartTimeRef.current);
      source.start(startAt);
      nextStartTimeRef.current = startAt + buffer.duration;
    },
    [ensureAudioContext]
  );

  const handleEvent = useCallback(
    (event: AssistantEvent) => {
      switch (event.kind) {
        case 'start':
          setState({ isResponding: true, query: event.query, responseText: '' });
          break;
        case 'text':
          // done=true는 스트림 종료 신호(빈 text)이므로 누적하지 않는다.
          if (!event.done && event.text) {
            setState((prev) => ({ ...prev, responseText: prev.responseText + event.text }));
          }
          break;
        case 'audio':
          playChunk(event.data, event.sampleRate);
          break;
        case 'end':
          setState((prev) => ({ ...prev, isResponding: false }));
          break;
      }
    },
    [playChunk]
  );

  useEffect(() => {
    if (!window.electronAPI?.onAssistantEvent) return;
    const off = window.electronAPI.onAssistantEvent(handleEvent);
    return off;
  }, [handleEvent]);

  useEffect(() => {
    return () => {
      audioCtxRef.current?.close().catch(() => {});
      audioCtxRef.current = null;
    };
  }, []);

  return state;
}

export default useAssistant;
