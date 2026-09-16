-- Corrige colunas de timestamp que estavam NOT NULL sem valor padrão no banco
-- (o default só existia no lado Python/SQLAlchemy, não no schema do Postgres,
-- o que quebra qualquer INSERT feito via SQL puro, como os scripts do web/).
--
-- Uso: cole este script no SQL Editor do Neon/Vercel Postgres, ou rode via psql.

ALTER TABLE users ALTER COLUMN created_at SET DEFAULT now();
ALTER TABLE push_subscriptions ALTER COLUMN created_at SET DEFAULT now();
ALTER TABLE settings ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE wallet_snapshots ALTER COLUMN timestamp SET DEFAULT now();
ALTER TABLE news_items ALTER COLUMN timestamp SET DEFAULT now();
ALTER TABLE opportunities ALTER COLUMN timestamp SET DEFAULT now();
ALTER TABLE committee_decisions ALTER COLUMN timestamp SET DEFAULT now();
ALTER TABLE positions ALTER COLUMN opened_at SET DEFAULT now();
ALTER TABLE trades ALTER COLUMN timestamp SET DEFAULT now();
ALTER TABLE api_cost_log ALTER COLUMN timestamp SET DEFAULT now();
