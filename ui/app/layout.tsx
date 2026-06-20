import type { Metadata } from "next";
import { IBM_Plex_Mono, Space_Grotesk } from "next/font/google";

import "./globals.css";

export const dynamic = "force-dynamic";

const headline = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-headline"
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono"
});

export const metadata: Metadata = {
  title: "Deep Agents Console",
  description: "Live prompt execution timeline for deep-agents."
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${headline.variable} ${mono.variable}`}>{children}</body>
    </html>
  );
}
