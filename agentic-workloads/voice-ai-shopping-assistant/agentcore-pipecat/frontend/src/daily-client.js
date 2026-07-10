/**
 * Daily client for the Aisle voice agent.
 *
 * Adapted from the reference tavus-pipecat-example daily-client.js. Differences:
 *  - /start is API Gateway and returns { room_url } (no token; public room).
 *  - Surfaces remote audio (and avatar video, if enabled) + bot data messages.
 */
import Daily from '@daily-co/daily-js';

export class DailyClient {
  constructor(startUrl) {
    this.startUrl = startUrl;
    this.callObject = null;
    this.remoteStream = new MediaStream();
    this.callbacks = {
      onTrack: null,
      onConnectionStateChange: null,
      onError: null,
      onBotMessage: null,
    };
  }

  async initializeLocalMedia() {
    // Pre-prompt for the mic so the permission dialog appears before joining.
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
  }

  /** POST /start → launch the bot into the Daily room → join it. */
  async connect() {
    const res = await fetch(this.startUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (!res.ok) {
      throw new Error(`/start failed: ${res.status} ${await res.text()}`);
    }
    const { room_url: roomUrl } = await res.json();
    if (!roomUrl) throw new Error('No room_url returned from /start');

    this.callObject = Daily.createCallObject({ audioSource: true, videoSource: false });
    this._signalledConnected = false;

    this.callObject.on('track-started', (ev) => {
      if (!ev.participant || ev.participant.local || !ev.track) return;
      this.remoteStream.addTrack(ev.track);
      this.callbacks.onTrack?.(this.remoteStream, ev.track.kind);
      // Consider "connected" once we hear/see the agent.
      if (!this._signalledConnected) {
        this._signalledConnected = true;
        this.callbacks.onConnectionStateChange?.('connected');
      }
    });

    this.callObject.on('left-meeting', () =>
      this.callbacks.onConnectionStateChange?.('disconnected'),
    );
    this.callObject.on('error', (ev) =>
      this.callbacks.onError?.(new Error(ev?.errorMsg || 'Daily error')),
    );

    // Bot data-channel events: transcripts + tool_result + order_progress.
    this.callObject.on('app-message', (ev) => {
      if (!ev?.data) return;
      const msg = typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data;
      this.callbacks.onBotMessage?.(msg);
    });

    await this.callObject.join({ url: roomUrl });
    return roomUrl;
  }

  disconnect() {
    if (this.callObject) {
      this.callObject.leave();
      this.callObject.destroy();
      this.callObject = null;
    }
  }

  toggleMicrophone(enabled) {
    this.callObject?.setLocalAudio(enabled);
    return enabled;
  }

  on(event, cb) {
    const key = `on${event.charAt(0).toUpperCase()}${event.slice(1)}`;
    if (Object.prototype.hasOwnProperty.call(this.callbacks, key)) {
      this.callbacks[key] = cb;
    }
  }
}
