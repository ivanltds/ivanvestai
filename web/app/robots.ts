import type { MetadataRoute } from "next";

// Achado 24/09/2026 (arquitetura-tecnica.md 9.20, Fase 5): dashboard fica
// acessível publicamente na internet (protegido só por login, ver seção 0/7
// da arquitetura), mas não tinha nenhum robots.txt/noindex -- em tese
// indexável por mecanismos de busca. Bloqueia tudo.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      disallow: "/",
    },
  };
}
