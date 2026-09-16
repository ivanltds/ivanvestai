import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { query } from "@/lib/db";
import SettingsForm from "./settings-form";

interface SettingRow { key: string; value: string; }

const DEFAULTS: Record<string, string> = {
  safety_stablecoin: "USDT",
  min_confidence_to_trade: "0.80",
  max_allocation_pct_per_trade: "0.50",
  daily_loss_alert_pct: "0.10",
  top_n_pairs: "100",
  cycle_interval_minutes: "15",
  display_currency: "BRL",
  bypass_macro_risk_window: "false",
};

export default async function SettingsPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  const rows = await query<SettingRow>(`select key, value from settings`);
  const current = { ...DEFAULTS, ...Object.fromEntries(rows.map((r) => [r.key, r.value])) };

  return (
    <div>
      <h1 style={{ fontSize: 20 }}>Configurações de trading</h1>
      <SettingsForm initial={current} />
    </div>
  );
}
