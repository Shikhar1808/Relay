import type { Metadata } from "next";
import { Chakra_Petch, Instrument_Sans } from "next/font/google";

import { AuthProvider } from "@/lib/auth";

import "./globals.css";

const chakra = Chakra_Petch({ variable: "--font-chakra", subsets: ["latin"], weight: ["300", "400", "500"] });
const instrument = Instrument_Sans({ variable: "--font-instrument", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Urumi",
  description: "Run your stores, your team and their access from one place.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${chakra.variable} ${instrument.variable} h-full antialiased`}>
      <body className="min-h-full">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
