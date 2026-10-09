import { useEffect, useRef, useState } from "react";
import "amazon-connect-streams";
import { PageHeader } from "@/components/layout/DashboardLayout";
import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import { HeadsetIcon } from "@/components/ui/icons";
import { loadConnectConfig } from "@/connect/config";

type Status = "loading" | "ready" | "unconfigured" | "error";

export default function ContactCenter() {
  const containerRef = useRef<HTMLDivElement>(null);
  const initedRef = useRef(false);
  const [status, setStatus] = useState<Status>("loading");
  const [message, setMessage] = useState<string>("");

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const cfg = await loadConnectConfig();
      if (cancelled) return;

      if (!cfg?.ccpUrl) {
        setStatus("unconfigured");
        return;
      }
      if (initedRef.current) return;
      if (!containerRef.current || !window.connect?.core) {
        setStatus("error");
        setMessage("The Amazon Connect Streams library failed to load.");
        return;
      }

      try {
        initedRef.current = true;
        window.connect.core.initCCP(containerRef.current, {
          ccpUrl: cfg.ccpUrl,
          loginPopup: true,
          loginPopupAutoClose: true,
          loginOptions: { autoClose: true, height: 600, width: 400 },
          region: cfg.region,
          softphone: {
            allowFramedSoftphone: true,
            // Web calls with video / screen sharing (opt-in connect-screenshare
            // module): the embedded CCP renders the call's video, and the screen
            // a merchant shares opens in a separate CCP window.
            allowFramedVideoCall: true,
            allowFramedScreenSharing: true,
            allowFramedScreenSharingPopUp: true,
          },
          pageOptions: {
            enableAudioDeviceSettings: true,
            enablePhoneTypeSettings: true,
          },
        });
        setStatus("ready");
      } catch (e) {
        setStatus("error");
        setMessage(e instanceof Error ? e.message : "Failed to initialize CCP.");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <PageHeader
        title="Contact Center"
        description="Amazon Connect agent panel (CCP) embedded in the dashboard — handles voice and live chat. Sign in with your Connect agent, go Available, and accept contacts here without leaving AnyCompanyPay."
      />

      {status === "unconfigured" && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
          Amazon Connect isn&apos;t configured for this environment yet.
        </div>
      )}
      {status === "error" && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {message}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader
            title="Agent panel"
            subtitle="Voice & chat (CCP)"
          />
          <CardBody className="flex justify-center">
            {/* CCP renders its iframe inside this container. ccp-v2 shows voice
                AND chat; the chat conversation opens inside this same panel. */}
            <div
              ref={containerRef}
              className="h-[720px] w-[400px] overflow-hidden rounded-lg border border-ink-100 bg-ink-50"
            />
          </CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader title="Getting started" subtitle="How to use the panel" />
          <CardBody>
            <ul className="space-y-3 text-sm text-ink-600">
              <li className="flex gap-2.5">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-600">
                  <HeadsetIcon className="h-3.5 w-3.5" />
                </span>
                A login popup opens the first time — sign in with your Amazon
                Connect <span className="font-medium">agent</span> credentials
                (separate from your AnyCompanyPay login).
              </li>
              <li className="flex gap-2.5">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-600 text-xs font-bold">
                  2
                </span>
                Set your status to <span className="font-medium">Available</span>{" "}
                in the panel to receive routed contacts (voice and chat).
              </li>
              <li className="flex gap-2.5">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-600 text-xs font-bold">
                  3
                </span>
                When a merchant chat arrives, click{" "}
                <span className="font-medium">Accept</span> — the conversation
                opens inside this panel and you can reply here. No need to open
                the Amazon Connect agent workspace.
              </li>
              <li className="flex gap-2.5">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-600 text-xs font-bold">
                  4
                </span>
                If the popup is blocked, allow popups for this site and reload.
              </li>
            </ul>
            <p className="mt-5 rounded-lg bg-ink-50 px-4 py-3 text-xs text-ink-500">
              The panel is embedded via the Amazon Connect Streams API (full
              <span className="font-medium"> ccp-v2</span>, so it handles voice
              and chat). This AnyCompanyPay app is registered as an approved origin on
              the Connect instance so the CCP can be framed here.
            </p>
            <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
              An agent can only have <span className="font-medium">one active
              CCP session</span>. If you&apos;re also signed into the Amazon
              Connect agent workspace in another tab, sign out of it — otherwise
              that session may receive the chat instead of this panel.
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
