"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Re-busca os dados do servidor a cada N segundos. Os nós <details> abertos
// continuam abertos (o estado vive no DOM e o React não mexe no atributo).
// Pausa enquanto a aba está em segundo plano, pra não gastar consultas ao banco.
export default function AutoRefresh({ seconds }: { seconds: number }) {
  const router = useRouter();

  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, seconds * 1000);
    return () => clearInterval(id);
  }, [router, seconds]);

  return null;
}
