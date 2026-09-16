"use client";

// Busca a cotação USDT->BRL a partir do NAVEGADOR (não do servidor da
// Vercel). Achado em 16/09/2026: rodando no servidor, o fetch pra Binance
// saía de uma função serverless na região padrão da Vercel (EUA), e a
// Binance bloqueia requisições de IP americano (451, Unavailable For Legal
// Reasons -- regra deles). No navegador do Ivan, que está no Brasil, isso
// não acontece.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getUsdtBrlRate, formatMoney as formatMoneyBase, type DisplayCurrency } from "@/lib/fx";

interface CurrencyState {
  currency: DisplayCurrency;
  brlRate: number | null;
  loading: boolean;
}

const CurrencyContext = createContext<CurrencyState>({
  currency: "USDT",
  brlRate: null,
  loading: false,
});

export function CurrencyProvider({
  currency,
  children,
}: {
  currency: DisplayCurrency;
  children: ReactNode;
}) {
  const [brlRate, setBrlRate] = useState<number | null>(null);
  const [loading, setLoading] = useState(currency === "BRL");

  useEffect(() => {
    if (currency !== "BRL") {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getUsdtBrlRate().then((rate) => {
      if (cancelled) return;
      setBrlRate(rate);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [currency]);

  return (
    <CurrencyContext.Provider value={{ currency, brlRate, loading }}>
      {children}
    </CurrencyContext.Provider>
  );
}

export function useCurrency() {
  return useContext(CurrencyContext);
}

/** Formata um valor em USDT na moeda ativa (BRL ou USDT). Enquanto a cotação
 * ainda não chegou do client, mostra em USDT pra não piscar "indisponível"
 * à toa -- só quando o fetch termina e falha de verdade é que cai no aviso. */
export function useFormatMoney() {
  const { currency, brlRate, loading } = useCurrency();
  const effectiveCurrency: DisplayCurrency = currency === "BRL" && loading ? "USDT" : currency;
  return (valueUsdt: number) => formatMoneyBase(valueUsdt, effectiveCurrency, brlRate);
}
