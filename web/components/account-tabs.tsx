import type { ActiveAccount } from "@/lib/accounts";

// Seletor de conta reaproveitado em /dashboard e /operations (multi-conta-plano.md,
// Fase E, ver 10.9). Visual igual ao seletor de /settings, mas com semântica
// diferente: aqui a opção sem conta selecionada é "todas as contas juntas" --
// a visão combinada de sempre, sem filtro nenhum -- e não "Padrão" (config
// global) como em /settings. Some sozinho com 1 conta só (nada a escolher).
export default function AccountTabs({
  basePath,
  accounts,
  selectedAccountId,
}: {
  basePath: string;
  accounts: ActiveAccount[];
  selectedAccountId: string | null;
}) {
  if (accounts.length < 2) return null;

  const tabStyle = (active: boolean): React.CSSProperties => ({
    fontSize: 13,
    padding: "5px 12px",
    borderRadius: 999,
    border: "1px solid var(--border)",
    textDecoration: "none",
    color: active ? "#fff" : "var(--text)",
    background: active ? "var(--accent)" : "transparent",
  });

  return (
    <div style={{ display: "flex", gap: 8, margin: "10px 0 14px", flexWrap: "wrap" }}>
      <a href={basePath} style={tabStyle(selectedAccountId === null)}>
        Todas as contas
      </a>
      {accounts.map((acc) => (
        <a key={acc.id} href={`${basePath}?account=${acc.id}`} style={tabStyle(selectedAccountId === acc.id)}>
          {acc.label}
          {acc.dry_run ? " (simulação)" : ""}
        </a>
      ))}
    </div>
  );
}
