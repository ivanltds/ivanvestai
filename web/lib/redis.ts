import { Redis } from "@upstash/redis";

export const redis = new Redis({
  url: process.env.UPSTASH_REDIS_REST_URL ?? "",
  token: process.env.UPSTASH_REDIS_REST_TOKEN ?? "",
});

export async function readRecentEvents(channel: string, limit = 20): Promise<Record<string, unknown>[]> {
  const raw = await redis.lrange(`events:${channel}`, 0, limit - 1);
  return raw.map((item) => (typeof item === "string" ? JSON.parse(item) : item));
}

export async function enqueueCommand(command: string, payload: Record<string, unknown> = {}) {
  await redis.lpush(
    "commands:bot",
    JSON.stringify({ command, payload, ts: Date.now() / 1000 })
  );
}

export async function getCached<T>(key: string): Promise<T | null> {
  const raw = await redis.get<string>(`cache:${key}`);
  if (!raw) return null;
  return typeof raw === "string" ? (JSON.parse(raw) as T) : (raw as T);
}
