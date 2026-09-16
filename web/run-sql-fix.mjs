#!/usr/bin/env node
/**
 * Roda o fix_timestamp_defaults.sql direto no Postgres, sem precisar do psql.
 * Uso: node run-sql-fix.mjs
 * (Rode a partir da pasta web/, com o mesmo DATABASE_URL já setado no shell)
 */
import { readFileSync } from "fs";
import pg from "pg";

const sql = readFileSync("../fix_timestamp_defaults.sql", "utf-8");

const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });

await pool.query(sql);

console.log("fix_timestamp_defaults.sql aplicado com sucesso.");
await pool.end();
