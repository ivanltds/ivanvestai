import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

// Achado 24/09/2026 (arquitetura-tecnica.md 9.20, Fase 5): reforça
// app/robots.ts com a tag/robots meta em toda página -- defesa em
// profundidade (alguns crawlers ignoram robots.txt mas respeitam a meta tag).
export const metadata: Metadata = {
  title: "IvanVestAI",
  description: "Dashboard do bot de trading cripto com comitê de agentes",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>
        <nav className="topnav">
          <Link href="/dashboard">Dashboard</Link>
          <Link href="/operations">Operações</Link>
          <Link href="/paper-trading">Paper Trading</Link>
          <Link href="/news">Notícias</Link>
          <Link href="/costs">Custos</Link>
          <Link href="/settings">Configurações</Link>
        </nav>
        <div className="container">{children}</div>
      </body>
    </html>
  );
}
