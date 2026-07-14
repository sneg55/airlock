import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Airlock approvals",
  description: "The human checkpoint every cloud change must pass.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <Link className="brand" href="/">
            <span className="brand-mark" aria-hidden="true">A</span>
            <span>
              <strong>Airlock</strong>
              <small>Operator checkpoint</small>
            </span>
          </Link>
          <div className="status"><span aria-hidden="true" /> Approval gate active</div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
