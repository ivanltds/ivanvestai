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

export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const from = req.nextUrl.searchParams.get("from");
  const to = req.nextUrl.searchParams.get("to");

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
    [r.timestamp, r.pair, r.side, r.order_type, r.quantity, r.price, r.fee, r.fee_asset, r.reason].join(",")
  );
  const csv = [header, ...csvLines].join("\n");

  return new NextResponse(csv, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="ivanvestai_trades.csv"`,
    },
  });
}
