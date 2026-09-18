import { NextRequest } from "next/server";
import { getSession } from "@/lib/auth";
import { readRecentEvents } from "@/lib/redis";

export const dynamic = "force-dynamic";

const CHANNELS = ["positions", "decisions", "news", "alerts"];
// Cada poll = 4 leituras no Upstash (uma por canal). A 4s por aba isso dava
// ~86 mil requisições/dia por aba aberta e estourava o free tier do Upstash; a
// 15s cai pra ~23 mil. O bot só publica eventos a cada ciclo (15 min), então a
// latência extra não importa.
const POLL_INTERVAL_MS = 15000;
// A conexão é encerrada de tempos em tempos (o EventSource do browser reconecta
// sozinho) -- evita função serverless pendurada e o Set `seen` crescer sem limite.
const MAX_CONNECTION_MS = 5 * 60 * 1000;
const MAX_SEEN = 500;

// SSE lendo do Redis por polling curto do lado do servidor (Upstash REST
// não tem SUBSCRIBE nativo de pub/sub). Mais simples de manter num
// ambiente serverless da Vercel do que WebSocket tradicional.
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return new Response("unauthorized", { status: 401 });

  const seen = new Set<string>();
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    async start(controller) {
      let closed = false;
      let interval: ReturnType<typeof setInterval> | undefined;
      let timeout: ReturnType<typeof setTimeout> | undefined;

      const close = () => {
        if (closed) return;
        closed = true;
        if (interval) clearInterval(interval);
        if (timeout) clearTimeout(timeout);
        try {
          controller.close();
        } catch {
          // já fechado
        }
      };

      const send = (event: string, data: unknown) => {
        if (closed) return;
        try {
          controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
        } catch {
          close(); // cliente desconectou
        }
      };

      send("connected", { ok: true });

      const poll = async () => {
        for (const channel of CHANNELS) {
          if (closed) return;
          try {
            const events = await readRecentEvents(channel, 10);
            for (const event of events.reverse()) {
              // a chave inclui o conteúdo: dois eventos com o mesmo `ts` não se apagam
              const key = `${channel}:${event.ts}:${JSON.stringify(event)}`;
              if (!seen.has(key)) {
                if (seen.size >= MAX_SEEN) seen.delete(seen.values().next().value as string);
                seen.add(key);
                send(channel, event);
              }
            }
          } catch {
            // Redis indisponível momentaneamente — próximo poll tenta de novo.
          }
        }
      };

      interval = setInterval(poll, POLL_INTERVAL_MS);
      timeout = setTimeout(close, MAX_CONNECTION_MS);

      req.signal.addEventListener("abort", close);
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
