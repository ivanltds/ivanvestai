"use client";

import { useState } from "react";
import { ALWAYS_GLOBAL_KEYS } from "@/lib/settings-shared";

const FIELDS: {
  key: string;
  label: string;
  type: string;
  options?: { value: string; label: string }[];
  warning?: string;
}[] = [
  {
    key: "display_currency",
    label: "Moeda de exibição no dashboard",
    type: "select",
    options: [
      { value: "BRL", label: "Real (BRL) -- convertido pela cotação da Binance" },
      { value: "USDT", label: "USDT / dólar (sem conversão)" },
    ],
  },
  { key: "safety_stablecoin", label: "Stablecoin de segurança", type: "text" },
  { key: "min_confidence_to_trade", label: "Confiança mínima do comitê (0-1)", type: "number" },
  { key: "max_allocation_pct_per_trade", label: "Teto de capital por operação (0-1)", type: "number" },
  { key: "daily_loss_alert_pct", label: "Alerta de perda diária (0-1)", type: "number" },
  { key: "top_n_pairs", label: "Top N pares por liquidez", type: "number" },
  { key: "cycle_interval_minutes", label: "Intervalo do ciclo (minutos)", type: "number" },
  {
    key: "strategy_profile",
    label: "Estratégia",
    type: "select",
    options: [
      { value: "final", label: "Final -- regras testadas no simulador (filtro BTC, stop por ATR, trailing)" },
      { value: "legacy", label: "Antiga -- comitê de IA decide (legacy)" },
    ],
  },
  { key: "final_max_fear_greed", label: "Final: só compra com Medo e Ganância até (0-100; 0 desliga)", type: "number" },
  { key: "final_pause_daily_loss_pct", label: "Final: pausa compras no dia com perda de (%; 0 desliga)", type: "number" },
  { key: "final_pause_drawdown_pct", label: "Final: pausa compras com queda do pico de (%; 0 desliga)", type: "number" },
  { key: "final_pause_days", label: "Final: duração da pausa por queda (dias)", type: "number" },
  {
    key: "bypass_macro_risk_window",
    label: "Desbloquear entradas novas durante janela de risco macro (FOMC/CPI)",
    type: "checkbox",
    warning:
      "⚠️ RISCO: por padrão o bot NÃO abre posições novas nas janelas de risco " +
      "macro (dia de decisão do FOMC, janela em torno de divulgação de CPI), " +
      "porque esses eventos costumam gerar volatilidade extrema e imprevisível " +
      "no mercado cripto. Ligando esta opção, o comitê passa a poder abrir " +
      "posições novas mesmo dentro dessas janelas. Isso NÃO desliga o dry run " +
      "nem muda a gestão de posições já abertas -- afeta só a entrada de " +
      "posições novas. Deixe desligado a menos que você entenda o risco e " +
      "queira testar isso conscientemente.",
  },
];

export default function SettingsForm({
  initial,
  initialUpdatedAt,
  accountId,
  overriddenKeys,
}: {
  initial: Record<string, string>;
  initialUpdatedAt: Record<string, string | null>;
  // multi-conta-plano.md, Fase E (ver 10.8): null = editando o Padrão
  // (account_id IS NULL); um uuid = editando o override dessa conta.
  accountId: string | null;
  // Chaves que já têm um override PRÓPRIO desta conta (ignorado em modo
  // Padrão, e sempre vazio pras chaves sempre-globais como display_currency).
  overriddenKeys: string[];
}) {
  const [values, setValues] = useState(initial);
  // Snapshot do `updated_at` que este formulário conhece por chave -- usado pro
  // controle de concorrência otimista em /api/settings (achado 24/09/2026,
  // arquitetura-tecnica.md 9.21 item 27: sem isso, salvar de duas abas/dispositivos
  // podia sobrescrever em silêncio uma mudança feita em outro lugar, inclusive pra
  // parâmetros de risco de capital real). Atualizado depois de um save bem
  // sucedido, pra um segundo save na mesma aba não conflitar consigo mesmo.
  const [knownUpdatedAt, setKnownUpdatedAt] = useState<Record<string, string | null>>(initialUpdatedAt);
  // Achado 24/09/2026 (arquitetura-tecnica.md 9.26, item 1): `save()` mandava o
  // objeto `values` INTEIRO sempre, não só o(s) campo(s) tocado(s) -- o UPSERT do
  // servidor roda por chave presente no corpo, então qualquer "Salvar" bumped o
  // updated_at de TODOS os campos, mesmo os intocados, e duas abas editando
  // campos DIFERENTES entravam em falso conflito de 409. `savedValues` é o
  // último estado conhecido como salvo (carregamento da página ou save anterior
  // bem-sucedido); só o que diverge dele é enviado.
  const [savedValues, setSavedValues] = useState(initial);
  const [overridden, setOverridden] = useState(new Set(overriddenKeys));
  const [status, setStatus] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);

  async function save() {
    setStatus("Salvando...");
    setConflict(false);
    const changed: Record<string, string> = {};
    for (const key of Object.keys(values)) {
      if (values[key] !== savedValues[key]) changed[key] = values[key];
    }
    if (Object.keys(changed).length === 0) {
      setStatus("Nada para salvar.");
      return;
    }
    const changedKnownUpdatedAt: Record<string, string | null> = {};
    for (const key of Object.keys(changed)) changedKnownUpdatedAt[key] = knownUpdatedAt[key] ?? null;
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values: changed, knownUpdatedAt: changedKnownUpdatedAt, accountId }),
    });
    if (res.ok) {
      const data = await res.json().catch(() => null);
      if (data?.updatedAt) setKnownUpdatedAt((prev) => ({ ...prev, ...data.updatedAt }));
      setSavedValues((prev) => ({ ...prev, ...changed }));
      if (accountId) {
        setOverridden((prev) => {
          const next = new Set(prev);
          for (const key of Object.keys(changed)) if (!ALWAYS_GLOBAL_KEYS.has(key)) next.add(key);
          return next;
        });
      }
      setStatus("Salvo.");
      return;
    }
    if (res.status === 409) {
      setConflict(true);
      const data = await res.json().catch(() => null);
      setStatus(
        data?.fields
          ? `Alterado em outro lugar desde que a página carregou: ${data.fields.join(", ")}. Recarregue a página.`
          : "Alterado em outro lugar desde que a página carregou. Recarregue a página."
      );
      return;
    }
    const data = await res.json().catch(() => null);
    setStatus(data?.fields ? `Valor inválido em: ${data.fields.join(", ")}` : "Erro ao salvar.");
  }

  return (
    <div className="card" style={{ display: "grid", gap: 12, maxWidth: 420 }}>
      {FIELDS.map((field) => {
        const isAccountOverride = accountId !== null && !ALWAYS_GLOBAL_KEYS.has(field.key);
        const overrideBadge = isAccountOverride && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              padding: "1px 6px",
              borderRadius: 999,
              border: "1px solid var(--border)",
              color: overridden.has(field.key) ? "var(--accent)" : "var(--muted)",
            }}
          >
            {overridden.has(field.key) ? "próprio desta conta" : "usando o Padrão"}
          </span>
        );

        if (field.type === "checkbox") {
          const checked = values[field.key] === "true";
          return (
            <div key={field.key} style={{ display: "grid", gap: 6 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={(e) => setValues({ ...values, [field.key]: e.target.checked ? "true" : "false" })}
                />
                {field.label}
                {overrideBadge}
              </label>
              {field.warning && checked && (
                <div
                  style={{
                    fontSize: 12,
                    lineHeight: 1.5,
                    padding: "8px 10px",
                    borderRadius: 6,
                    background: "rgba(220, 38, 38, 0.1)",
                    border: "1px solid rgba(220, 38, 38, 0.4)",
                    color: "#b91c1c",
                  }}
                >
                  {field.warning}
                </div>
              )}
            </div>
          );
        }
        return (
          <label key={field.key} style={{ display: "grid", gap: 4, fontSize: 13 }}>
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              {field.label}
              {overrideBadge}
            </span>
            {field.type === "select" ? (
              <select
                value={values[field.key] ?? ""}
                onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
              >
                {field.options?.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            ) : (
              <input
                type={field.type}
                step="0.01"
                value={values[field.key] ?? ""}
                onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
              />
            )}
          </label>
        );
      })}
      <button className="primary" onClick={save}>Salvar</button>
      {conflict && (
        <button type="button" onClick={() => window.location.reload()}>
          Recarregar página
        </button>
      )}
      {status && (
        <span style={{ fontSize: 12, color: conflict ? "#b91c1c" : "var(--muted)" }}>{status}</span>
      )}
    </div>
  );
}
