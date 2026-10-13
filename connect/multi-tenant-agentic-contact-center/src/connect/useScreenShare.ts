import { useCallback, useEffect, useRef, useState } from "react";
import { getScreenShareStatus, startScreenShare } from "./screenShareApi";

/**
 * Merchant side of an Amazon Connect web call with screen sharing.
 *
 *   start(caseId?) -> backend StartWebRTCContact -> join the call's Amazon Chime
 *                     SDK meeting with audio -> wait for the agent (the backend
 *                     reports when Connect has connected an agent to the contact;
 *                     meeting presence isn't used, as Connect's own media
 *                     participant is present before any agent answers)
 *   share()        -> share the screen (browser picker) as Chime content share
 *   stopShare()    -> stop sharing, stay on the call
 *   hangUp()       -> leave the call (ends the contact for the merchant)
 *
 * The Chime SDK is imported dynamically so it only loads when a merchant
 * actually starts a session (same approach as ChatJS).
 */
export type ScreenShareStatus =
  | "idle"
  | "starting" // calling the backend + joining audio
  | "waiting" // in queue, no agent yet
  | "connected" // agent joined
  | "ended"
  | "error";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AudioVideo = any;

export function useScreenShare() {
  const [status, setStatus] = useState<ScreenShareStatus>("idle");
  const [sharing, setSharing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [contactId, setContactId] = useState<string | null>(null);
  const avRef = useRef<AudioVideo | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const cleanup = useCallback(() => {
    const av = avRef.current;
    avRef.current = null;
    if (!av) return;
    try {
      av.stopContentShare();
    } catch {
      /* not sharing */
    }
    try {
      av.unbindAudioElement();
      av.stop();
    } catch {
      /* already stopped */
    }
  }, []);

  useEffect(() => cleanup, [cleanup]);

  // While the call is live, ask the backend whether an agent is connected yet.
  useEffect(() => {
    if (!contactId || (status !== "waiting" && status !== "connected")) return;
    let stopped = false;
    const poll = async () => {
      try {
        const state = await getScreenShareStatus(contactId);
        if (stopped) return;
        if (state === "ended") {
          cleanup();
          setSharing(false);
          setStatus("ended");
        } else {
          setStatus(state);
        }
      } catch {
        /* transient; try again on the next tick */
      }
    };
    void poll();
    const t = setInterval(() => void poll(), 3000);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [contactId, status, cleanup]);

  const start = useCallback(
    async (caseId?: string) => {
      setError(null);
      setStatus("starting");
      try {
        const res = await startScreenShare({ caseId });
        setContactId(res.contactId);
        const chime = await import("amazon-chime-sdk-js");
        const logger = new chime.ConsoleLogger("ScreenShare", chime.LogLevel.WARN);
        const deviceController = new chime.DefaultDeviceController(logger);
        const configuration = new chime.MeetingSessionConfiguration({ Meeting: res.meeting }, { Attendee: res.attendee });
        const session = new chime.DefaultMeetingSession(configuration, logger, deviceController);
        const av = session.audioVideo;
        avRef.current = av;

        // Voice: first microphone (the browser asks for permission).
        const inputs = await av.listAudioInputDevices();
        await av.startAudioInput(inputs[0]?.deviceId ?? null);
        if (!audioRef.current) audioRef.current = new Audio();
        await av.bindAudioElement(audioRef.current);

        av.addObserver({
          audioVideoDidStop: (sessionStatus: { statusCode: () => number }) => {
            const code = sessionStatus.statusCode();
            // Agent/contact ended the call, the merchant left, or was removed.
            const C = chime.MeetingSessionStatusCode;
            const ended = [C.MeetingEnded, C.Left, C.AudioAttendeeRemoved, C.AudioAuthenticationRejected].includes(code);
            setSharing(false);
            setStatus(ended ? "ended" : "error");
            if (!ended) setError(`Call stopped (status ${code})`);
            avRef.current = null;
          },
        });
        av.addContentShareObserver({
          contentShareDidStart: () => setSharing(true),
          contentShareDidStop: () => setSharing(false),
        });

        av.start();
        setStatus("waiting");
      } catch (e) {
        cleanup();
        setStatus("error");
        setError(e instanceof Error ? e.message : "Failed to start screen sharing");
      }
    },
    [cleanup]
  );

  const share = useCallback(async () => {
    const av = avRef.current;
    if (!av) return;
    setError(null);
    try {
      await av.startContentShareFromScreenCapture();
    } catch (e) {
      // The merchant cancelled the browser's screen picker, or permission was denied.
      setError(e instanceof Error && e.name !== "NotAllowedError" ? e.message : "Screen sharing was not started");
    }
  }, []);

  const stopShare = useCallback(() => {
    try {
      avRef.current?.stopContentShare();
    } catch {
      /* not sharing */
    }
  }, []);

  const hangUp = useCallback(() => {
    cleanup();
    setSharing(false);
    setStatus("ended");
  }, [cleanup]);

  return { status, sharing, error, contactId, start, share, stopShare, hangUp };
}
