// Conversão USDT -> BRL. Usa a cotação pública da Binance (par USDTBRL,
// endpoint de mercado, sem precisar de API key) em vez de um serviço de FX
// separado -- mesma fonte de verdade de preço que o resto do sistema usa.
//
// Cacheada em memória do processo por alguns minutos: no ambiente serverless
// da Vercel isso funciona "às vezes" (uma instância quente reaproveita,
// uma instância fria começa do zero) -- é só pra não bater na Binance a
// cada carregamento de página, não uma garantia de baixa latência.
//
// Achado em 16/09/2026 (arquitetura-tecnica.md 9.7): o dashboard mostrava
// "Balanço geral (BRL)" mas nunca convertia nada de verdade -- só formatava
// o número em USD como se fosse BRL, o que nunca bateria com o saldo real
// em reais que o Ivan vê no app da Binance.

let cachedRate: { value: number; at: number } | null = null;
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 min

export async function getUsdtBrlRate(): Promise<number | null> {
  if (cachedRate && Date.now() - cachedRate.at < CACHE_TTL_MS) {
    return cachedRate.value;
  }
  try {
    const res = await fetch("https://api.binance.com/api/v3/ticker/price?symbol=USDTBRL", {
      cache: "no-store",
    });
    if (!res.ok) return cachedRate?.value ?? null;
    const data = (await res.json()) as { price?: string };
    const rate = data.price ? parseFloat(data.price) : NaN;
    if (!rate || Number.isNaN(rate)) return cachedRate?.value ?? null;
    cachedRate = { value: rate, at: Date.now() };
    return rate;
  } catch {
    // Rede fora ou Binance instável -- usa o último valor bom conhecido
    // (se tiver) em vez de quebrar a página.
    return cachedRate?.value ?? null;
  }
}

export type DisplayCurrency = "BRL" | "USDT";

export async function resolveDisplayCurrency(raw: string | undefined): Promise<{
  currency: DisplayCurrency;
  brlRate: number | null;
}> {
  const currency: DisplayCurrency = raw === "USDT" ? "USDT" : "BRL"; // BRL é o default
  const brlRate = currency === "BRL" ? await getUsdtBrlRate() : null;
  return { currency, brlRate };
}

/** Formata um valor em USDT/USD na moeda escolhida. Se BRL foi pedido mas a
 * cotação não pôde ser obtida, cai pra USDT em vez de mostrar um valor errado. */
export function formatMoney(valueUsdt: number, currency: DisplayCurrency, brlRate: number | null): string {
  if (currency === "BRL" && brlRate) {
    return (valueUsdt * brlRate).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }
  return valueUsdt.toLocaleString("pt-BR", { style: "currency", currency: "USD" }).replace("US$", "USDT");
}
