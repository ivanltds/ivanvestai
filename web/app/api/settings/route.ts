import { randomUUID } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/auth";
import { pool } from "@/lib/db";
import { ALWAYS_GLOBAL_KEYS } from "@/lib/settings-shared";

// Só estas chaves são aceitas (as mesmas do formulário de /settings), cada uma
// com sua validação. Antes qualquer chave/valor era gravado -- inclusive
// `bot_status` (o formulário reenviava o valor de quando a página carregou e
// podia religar um bot pausado pelo kill switch) e valores absurdos como
// max_allocation_pct_per_trade=5. O bot também revalida as faixas
// (bot/core/config_store.py).
type Validator = (value: string) => boolean;

const num =
  (min: number, max: number, integer = false): Validator =>
  (value) => {
    const n = Number(value);
    return value.trim() !== "" && Number.isFinite(n) && n >= min && n <= max && (!integer || Number.isInteger(n));
  };

const VALIDATORS: Record<string, Validator> = {
  display_currency: (v) => v === "BRL" || v === "USDT",
  safety_stablecoin: (v) => /^[A-Z0-9]{2,10}$/.test(v),
  min_confidence_to_trade: num(0.5, 1),
  max_allocation_pct_per_trade: num(0.01, 1),
  daily_loss_alert_pct: num(0, 1),
  top_n_pairs: num(1, 500, true),
  cycle_interval_minutes: num(1, 1440, true),
  bypass_macro_risk_window: (v) => v === "true" || v === "false",
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface SettingsPostBody {
  values: Record<string, unknown>;
  // Achado 24/09/2026 (arquitetura-tecnica.md 9.21 item 27): nada aqui detectava
  // duas gravações concorrentes (duas abas abertas, ou form em dois dispositivos)
  // -- a segunda a commitar sobrescrevia a primeira em silêncio, sem aviso, mesmo
  // pra parâmetros que afetam risco de capital real (max_allocation_pct_per_trade,
  // daily_loss_alert_pct etc). `knownUpdatedAt` é o `updated_at` que o cliente viu
  // por último pra cada chave que está gravando (null = achava que a linha ainda
  // não existia -- ver `accountId` abaixo pra qual linha, exatamente), comparado
  // contra o valor atual, sob lock de linha, pra fechar a janela de corrida entre
  // checagem e escrita.
  knownUpdatedAt: Record<string, string | null>;
  // multi-conta-plano.md, Fase E (ver 10.8): null = gravando os valores "master"
  // (account_id IS NULL na tabela settings -- o padrão que vale pra quem não tiver
  // override próprio); um uuid = gravando um OVERRIDE só dessa conta (account_id =
  // esse id). `display_currency` é a exceção que sempre vai pro master, mesmo
  // com uma conta selecionada (ver ALWAYS_GLOBAL_KEYS) -- é só a moeda de exibição
  // do dashboard agregado, não faz sentido divergir por conta.
  accountId: string | null;
}

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Requisição inválida" }, { status: 400 });
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return NextResponse.json({ error: "Requisição inválida" }, { status: 400 });
  }
  const { values, knownUpdatedAt, accountId: rawAccountId } = body as Partial<SettingsPostBody>;
  if (!values || typeof values !== "object" || Array.isArray(values)) {
    return NextResponse.json({ error: "Requisição inválida" }, { status: 400 });
  }
  if (rawAccountId !== null && rawAccountId !== undefined && !UUID_RE.test(rawAccountId)) {
    return NextResponse.json({ error: "accountId inválido" }, { status: 400 });
  }
  const scopeAccountId: string | null = rawAccountId ?? null;

  // Confirma que a conta existe e está ativa -- nunca grava um override "solto"
  // pra um id que não existe mais (ex: conta desativada depois da página carregar,
  // ou id inventado na requisição).
  if (scopeAccountId) {
    const acc = await pool.query(`select 1 from accounts where id = $1 and is_active = true`, [scopeAccountId]);
    if (acc.rowCount === 0) {
      return NextResponse.json({ error: "Conta não encontrada ou inativa" }, { status: 400 });
    }
  }

  // entries: [key, value, account_id-DA-LINHA-que-vai-ser-gravada] -- quase
  // sempre igual a scopeAccountId, EXCETO display_currency, que sempre vai pro
  // master (null), mesmo editando com uma conta selecionada.
  const entries: [string, string, string | null][] = [];
  const errors: string[] = [];
  for (const [key, raw] of Object.entries(values)) {
    const validator = VALIDATORS[key];
    if (!validator) continue; // chave não editável por aqui (ex: bot_status) -- ignorada
    const value = String(raw ?? "").trim();
    if (!validator(value)) {
      errors.push(key);
      continue;
    }
    const rowAccountId = ALWAYS_GLOBAL_KEYS.has(key) ? null : scopeAccountId;
    entries.push([key, value, rowAccountId]);
  }

  if (errors.length > 0) {
    return NextResponse.json({ error: "Valores inválidos", fields: errors }, { status: 400 });
  }
  if (entries.length === 0) {
    return NextResponse.json({ ok: true, updatedAt: {} });
  }

  const mapKey = (key: string, accountId: string | null) => `${accountId ?? "master"}::${key}`;

  const client = await pool.connect();
  try {
    await client.query("BEGIN");

    // Trava as linhas envolvidas ANTES de comparar -- por (key, account_id) agora,
    // não só key (achado 25/09/2026, Fase E: com chave composta, a MESMA key pode
    // ter uma linha master e uma linha por conta ao mesmo tempo -- travar só por
    // key travaria/compararia a linha errada).
    const currentByMapKey = new Map<string, string>();
    async function lockAndRecord(key: string, accountId: string | null) {
      const mk = mapKey(key, accountId);
      if (currentByMapKey.has(mk)) return;
      const res = accountId
        ? await client.query<{ updated_at: Date }>(
            `select updated_at from settings where key = $1 and account_id = $2 for update`,
            [key, accountId]
          )
        : await client.query<{ updated_at: Date }>(
            `select updated_at from settings where key = $1 and account_id is null for update`,
            [key]
          );
      if (res.rows[0]) currentByMapKey.set(mk, res.rows[0].updated_at.toISOString());
    }
    for (const [key, , accountId] of entries) await lockAndRecord(key, accountId);

    // Achado 24/09/2026 (arquitetura-tecnica.md 9.26, item 2): `for update` acima
    // só tranca linha que JÁ EXISTE -- pra uma linha nunca salva (sem linha na
    // tabela ainda) não havia o que travar, e duas gravações concorrentes da
    // MESMA (key, account_id) nova podiam passar as duas pela checagem de conflito
    // (current===null, known===null) e a que fizesse o INSERT por último
    // sobrescrevia a outra sem o 409 que a garantia deveria dar. Trava
    // consultiva por (key, account_id) fecha essa janela antes de reconferir.
    const newEntries = entries.filter(([key, , accountId]) => !currentByMapKey.has(mapKey(key, accountId)));
    for (const [key, , accountId] of newEntries) {
      await client.query(`select pg_advisory_xact_lock(hashtext($1))`, [mapKey(key, accountId)]);
    }
    for (const [key, , accountId] of newEntries) await lockAndRecord(key, accountId);

    const conflicts: string[] = [];
    for (const [key, , accountId] of entries) {
      const current = currentByMapKey.get(mapKey(key, accountId)) ?? null;
      const known = knownUpdatedAt?.[key] ?? null;
      if (current !== known) conflicts.push(key);
    }
    if (conflicts.length > 0) {
      await client.query("ROLLBACK");
      return NextResponse.json(
        {
          error: "Essas configurações foram alteradas em outro lugar desde que a página carregou. Recarregue e tente de novo.",
          fields: conflicts,
        },
        { status: 409 }
      );
    }

    const updatedAt: Record<string, string> = {};
    for (const [key, value, accountId] of entries) {
      // Achado 24/09/2026 (arquitetura-tecnica.md 9.26, item 1): combinado com o
      // fix do formulário (só envia campos tocados), este `case` é uma segunda
      // camada -- só bumpa updated_at quando o valor de fato muda, então um
      // save que reenviar um valor idêntico ao já salvo nunca gera um falso
      // conflito de 409 pra outra aba que não tocou esse campo.
      //
      // `id` é gerado aqui (randomUUID) porque a coluna não tem default no banco
      // (o default uuid4() de db/models.py é só do lado do SQLAlchemy) -- em
      // conflito o id da linha existente nunca é tocado, só value/updated_at.
      // `on conflict` mira o índice único parcial certo pro escopo (global ou
      // por conta) -- ver db/models.py (classe Setting) e
      // migrate_add_settings_composite_key.py pro porquê de dois índices
      // parciais em vez de uma UNIQUE composta comum.
      const result = accountId
        ? await client.query<{ updated_at: Date }>(
            `insert into settings (id, key, value, account_id) values ($1, $2, $3, $4)
             on conflict (account_id, key) where account_id is not null do update set
               value = excluded.value,
               updated_at = case when settings.value is distinct from excluded.value then now() else settings.updated_at end
             returning updated_at`,
            [randomUUID(), key, value, accountId]
          )
        : await client.query<{ updated_at: Date }>(
            `insert into settings (id, key, value, account_id) values ($1, $2, $3, null)
             on conflict (key) where account_id is null do update set
               value = excluded.value,
               updated_at = case when settings.value is distinct from excluded.value then now() else settings.updated_at end
             returning updated_at`,
            [randomUUID(), key, value]
          );
      updatedAt[key] = result.rows[0].updated_at.toISOString();
    }
    await client.query("COMMIT");
    return NextResponse.json({ ok: true, updatedAt });
  } catch {
    await client.query("ROLLBACK");
    return NextResponse.json({ error: "falha ao salvar" }, { status: 500 });
  } finally {
    client.release();
  }
}
