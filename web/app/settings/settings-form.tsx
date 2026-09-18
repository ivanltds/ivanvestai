"use client";

import { useState } from "react";

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

export default function SettingsForm({ initial }: { initial: Record<string, string> }) {
  const [values, setValues] = useState(initial);
  const [status, setStatus] = useState<string | null>(null);

  async function save() {
    setStatus("Salvando...");
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
    if (res.ok) {
      setStatus("Salvo.");
      return;
    }
    const data = await res.json().catch(() => null);
    setStatus(data?.fields ? `Valor inválido em: ${data.fields.join(", ")}` : "Erro ao salvar.");
  }

  return (
    <div className="card" style={{ display: "grid", gap: 12, maxWidth: 420 }}>
      {FIELDS.map((field) => {
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
            {field.label}
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
      {status && <span style={{ fontSize: 12, color: "var(--muted)" }}>{status}</span>}
    </div>
  );
}
