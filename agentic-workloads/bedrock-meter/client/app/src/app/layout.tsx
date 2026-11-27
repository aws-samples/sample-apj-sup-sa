import type { Metadata } from "next";
import { Providers } from "@/app/providers";
import { AppShell } from "@/components/layout/AppShell";
import "@/app/globals.css";

export const metadata: Metadata = {
  title: "Bedrock Meter Console",
  description: "Operational console for Bedrock usage metering and quota controls",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
