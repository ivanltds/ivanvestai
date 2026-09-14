import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'IvanvestAI',
  description: 'Terminal Quantitativo',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="pt-BR">
      <body className="antialiased bg-neutral-950 text-neutral-200">
        {children}
      </body>
    </html>
  )
}
