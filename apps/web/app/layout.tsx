import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Shell } from "@/components/shell/Shell";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "CLIPX AI", template: "%s · CLIPX AI" },
  description: "Autonomous AI clipping and short-form publishing for Lacy's Twitch streams.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { themeColor: "#050506", colorScheme: "dark" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
