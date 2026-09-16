"use client";

import { useState } from "react";

const FIELDS: { key: string; label: string; type: string }[] = [
  { key: "safety_stablecoin", label: "Stablecoin de segurança", type: "text" },
  { key: "min_confidence_to_trade", label: "Confiança mínima do comitê (0-1)", type: "number" },
  { key: "max_allocation_pct_per_trade", label: "Teto de capital por operação (0-1)", type: "number" },
  { key: "daily_loss_alert_pct", label: "Alerta de perda diária (0-1)", type: "number" },
  { key: "top_n_pairs", label: "Top N pares por liquidez", type: "number" },
  { key: "cycle_interval_minutes", label: "Intervalo do ciclo (minutos)", type: "number" },
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
    setStatus(res.ok ? "Salvo." : "Erro ao salvar.");
  }

  return (
    <div className="card" style={{ display: "grid", gap: 12, maxWidth: 420 }}>
      {FIELDS.map((field) => (
        <label key={field.key} style={{ display: "grid", gap: 4, fontSize: 13 }}>
          {field.label}
          <input
            type={field.type}
            step="0.01"
            value={values[field.key] ?? ""}
            onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
          />
        </label>
      ))}
      <button className="primary" onClick={save}>Salvar</button>
      {status && <span style={{ fontSize: 12, color: "var(--muted)" }}>{status}</span>}
    </div>
  );
}
