import "./globals.css";
import type { Metadata, Viewport } from "next";

export const metadata: Metadata = {
  title: "DataFoundry 看板",
  description: "每一条数据的去留都有可辩护的理由",
};
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh">
      <body>
        <div className="shell">{children}</div>
      </body>
    </html>
  );
}
