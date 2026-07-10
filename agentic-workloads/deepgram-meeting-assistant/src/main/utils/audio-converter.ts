import { INT16_MIN_ABS, INT16_MAX } from '../constants';

export function float32ToInt16Buffer(float32Array: Float32Array): Buffer {
  const int16Array = new Int16Array(float32Array.length);

  for (let i = 0; i < float32Array.length; i++) {
    const sample = Math.max(-1, Math.min(1, float32Array[i]));
    int16Array[i] = sample < 0 ? sample * INT16_MIN_ABS : sample * INT16_MAX;
  }

  return Buffer.from(int16Array.buffer);
}

export function base64ToBuffer(base64: string): Buffer {
  return Buffer.from(base64, 'base64');
}

export function bufferToBase64(buffer: Buffer): string {
  return buffer.toString('base64');
}
