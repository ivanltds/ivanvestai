import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth";
import { query } from "@/lib/db";

interface NewsRow {
  id: string;
  source: string;
  summary_pt: string;
  sentiment_score: number;
  url: string;
  timestamp: string;
}

export default async function NewsPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  const items = await query<NewsRow>(
    `select id, source, summary_pt, sentiment_score, url, timestamp
     from news_items order by timestamp desc limit 50`
  );

  return (
    <div>
      <h1 style={{ fontSize: 20 }}>Feed de notícias</h1>
      {items.length === 0 && <p style={{ color: "var(--muted)" }}>Nenhuma notícia coletada ainda.</p>}
      {items.map((item) => (
        <div key={item.id} className="card">
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--muted)" }}>
            <span>
              {item.source} — {new Date(item.timestamp).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" })}
            </span>
            <span className={item.sentiment_score >= 0 ? "positive" : "negative"}>
              {item.sentiment_score >= 0 ? "+" : ""}{item.sentiment_score.toFixed(2)}
            </span>
          </div>
          <p>{item.summary_pt}</p>
          <a href={item.url} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
            fonte original
          </a>
        </div>
      ))}
    </div>
  );
}
