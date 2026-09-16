import { Pool } from "pg";

// Mesmo Postgres usado pelo bot/ (fonte de verdade compartilhada).
// Reaproveita a conexão entre requests em dev (hot reload) via globalThis.
declare global {
  // eslint-disable-next-line no-var
  var _ivanvestaiPool: Pool | undefined;
}

export const pool =
  globalThis._ivanvestaiPool ??
  new Pool({
    connectionString: process.env.DATABASE_URL,
    max: 5,
  });

if (process.env.NODE_ENV !== "production") {
  globalThis._ivanvestaiPool = pool;
}

export async function query<T = unknown>(text: string, params?: unknown[]): Promise<T[]> {
  const result = await pool.query(text, params);
  return result.rows as T[];
}
