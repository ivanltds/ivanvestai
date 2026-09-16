import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "IvanVestAI",
  description: "Dashboard do bot de trading cripto com comitê de agentes",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>
        <nav className="topnav">
          <Link href="/dashboard">Dashboard</Link>
          <Link href="/paper-trading">Paper Trading</Link>
          <Link href="/news">Notícias</Link>
          <Link href="/settings">Configurações</Link>
        </nav>
        <div className="container">{children}</div>
      </body>
    </html>
  );
}
