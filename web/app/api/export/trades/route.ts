import { NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/auth";
import { query } from "@/lib/db";

interface TradeRow {
  timestamp: string;
  pair: string;
  side: string;
  order_type: string;
  quantity: number;
  price: number;
  fee: number;
  fee_asset: string;
  reason: string;
}

// Escapa um campo CSV (aspas, vírgula, quebra de linha) e neutraliza "fórmulas"
// (=, +, @, - seguido de texto) pra planilha não executar o conteúdo.
function csvField(value: unknown): string {
  let text = value === null || value === undefined ? "" : value instanceof Date ? value.toISOString() : String(value);
  if (/^[=+@]/.test(text) || (text.startsWith("-") && Number.isNaN(Number(text)))) text = `'${text}`;
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

// null = sem filtro; undefined = data inválida
function parseDateParam(value: string | null): string | null | undefined {
  if (!value) return null;
  return Number.isNaN(Date.parse(value)) ? undefined : value;
}

export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const from = parseDateParam(req.nextUrl.searchParams.get("from"));
  const to = parseDateParam(req.nextUrl.searchParams.get("to"));
  if (from === undefined || to === undefined) {
    return NextResponse.json({ error: "datas inválidas (use ISO 8601)" }, { status: 400 });
  }

  // is_paper = false: exportação é pra uso real (ex: declaração de IR) --
  // nunca deve incluir trades simulados do dry-run (ver arquitetura-tecnica.md 9.6).
  const rows = await query<TradeRow>(
    `select timestamp, pair, side, order_type, quantity, price, fee, fee_asset, reason
     from trades
     where is_paper = false
       and ($1::timestamptz is null or timestamp >= $1)
       and ($2::timestamptz is null or timestamp <= $2)
     order by timestamp desc`,
    [from, to]
  );

  const header = "timestamp,pair,side,order_type,quantity,price,fee,fee_asset,reason";
  const csvLines = rows.map((r) =>
    [r.timestamp, r.pair, r.side, r.order_type, r.quantity, r.price, r.fee, r.fee_asset, r.reason]
      .map(csvField)
      .join(",")
  );
  const csv = [header, ...csvLines].join("\n");

  return new NextResponse(csv, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="ivanvestai_trades.csv"`,
    },
  });
}
