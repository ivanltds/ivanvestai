import { NextRequest } from "next/server";
import { getSession } from "@/lib/auth";
import { readRecentEvents } from "@/lib/redis";

export const dynamic = "force-dynamic";

const CHANNELS = ["positions", "decisions", "news", "alerts"];
const POLL_INTERVAL_MS = 4000;

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
      const send = (event: string, data: unknown) => {
        controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
      };

      send("connected", { ok: true });

      const interval = setInterval(async () => {
        for (const channel of CHANNELS) {
          try {
            const events = await readRecentEvents(channel, 10);
            for (const event of events.reverse()) {
              const key = `${channel}:${event.ts}`;
              if (!seen.has(key)) {
                seen.add(key);
                send(channel, event);
              }
            }
          } catch {
            // Redis indisponível momentaneamente — próximo poll tenta de novo.
          }
        }
      }, POLL_INTERVAL_MS);

      req.signal.addEventListener("abort", () => {
        clearInterval(interval);
        controller.close();
      });
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
