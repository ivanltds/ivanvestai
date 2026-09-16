#!/usr/bin/env node
/**
 * Cria (ou atualiza a senha d)o usuário do dashboard.
 * Uso: DATABASE_URL=... node scripts/create-user.mjs seuemail@exemplo.com "sua-senha"
 */
import { randomUUID, randomBytes, scryptSync } from "crypto";
import pg from "pg";

const [, , email, password] = process.argv;

if (!email || !password) {
  console.error("Uso: node scripts/create-user.mjs <email> <senha>");
  process.exit(1);
}

function hashPassword(plain) {
  const salt = randomBytes(16).toString("hex");
  const hash = scryptSync(plain, salt, 64).toString("hex");
  return `${salt}:${hash}`;
}

const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });

const passwordHash = hashPassword(password);

await pool.query(
  `insert into users (id, email, password_hash, created_at) values ($1, $2, $3, now())
   on conflict (email) do update set password_hash = excluded.password_hash`,
  [randomUUID(), email, passwordHash]
);

console.log(`Usuário ${email} criado/atualizado com sucesso.`);
await pool.end();
