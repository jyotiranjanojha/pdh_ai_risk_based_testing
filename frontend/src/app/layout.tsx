import type { Metadata } from "next";
import "./globals.css";
import { AppProvider } from "@/components/AppContext";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "Enterprise AI Risk-Based Testing Platform",
  description: "Zero-data-egress Snowflake + Cortex AI Risk-Based Testing Control Plane",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <AppProvider>
          <AppShell>{children}</AppShell>
        </AppProvider>
      </body>
    </html>
  );
}
