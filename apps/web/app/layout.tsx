import type { Metadata, Viewport } from "next";
import { Cormorant_Garamond, JetBrains_Mono, Manrope } from "next/font/google";
import { Providers } from "@/components/providers";
import { themeScript } from "@/components/shell/theme";
import "./globals.css";

const cormorant = Cormorant_Garamond({ subsets: ["latin"], weight: ["400", "500", "600", "700"], style: ["normal", "italic"], variable: "--font-cormorant", display: "swap" });
const manrope = Manrope({ subsets: ["latin"], variable: "--font-manrope", display: "swap" });
const jetbrains = JetBrains_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-jetbrains", display: "swap" });

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://datapilot-analyst.vercel.app"),
  title: { default: "DataPilot — verified answers from your data", template: "%s · DataPilot" },
  description:
    "A team of AI agents that plans, writes, votes on and verifies SQL, runs Python in your browser, and checks every number against the data.",
  openGraph: { title: "DataPilot", description: "Ask in plain English. Get answers you can verify.", type: "website" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#e9e7df" },
    { media: "(prefers-color-scheme: dark)", color: "#0e1030" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${cormorant.variable} ${manrope.variable} ${jetbrains.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-dvh">
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-full focus:bg-navy focus:px-4 focus:py-2 focus:text-ivory">
          Skip to content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
