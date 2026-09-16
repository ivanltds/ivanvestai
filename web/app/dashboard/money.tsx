"use client";

import { useFormatMoney, useCurrency } from "./currency-context";

/** Mostra um valor (em USDT) formatado na moeda ativa. A cotação é buscada
 * no client (ver currency-context.tsx), então no primeiro render (SSR e
 * antes do fetch terminar) aparece em USDT e depois atualiza pra BRL. */
export default function Money({ usdt }: { usdt: number }) {
  const format = useFormatMoney();
  return <>{format(usdt)}</>;
}

/** Aviso de "cotação indisponível" -- só aparece depois que o client
 * REALMENTE tentou buscar a cotação da Binance e falhou, nunca durante o
 * carregamento inicial. */
export function CurrencyUnavailableNotice() {
  const { currency, brlRate, loading } = useCurrency();
  if (currency !== "BRL" || loading || brlRate) return null;
  return (
    <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 4 }}>
      cotação BRL indisponível agora -- mostrando USDT
    </div>
  );
}
