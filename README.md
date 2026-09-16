# IvanVestAI

Bot de trading de criptomoedas (Binance Spot) orientado por um comitê de agentes de IA, com dashboard de acompanhamento em Next.js.

> Especificação completa das decisões de produto, estratégias e arquitetura vive no projeto Claude "ivanvestai" (`mvp-spec.md`, `indicadores-estrategias.md`, `arquitetura-tecnica.md`). Este repositório é a implementação dessas decisões.

## Visão geral da topologia

- **`web/`** — dashboard Next.js, hospedado na Vercel (`https://ivanvestai.vercel.app`). Root de deploy = esta pasta.
- **`bot/`** — bot Python, roda local (Windows) 24/7. Faz a análise de mercado/notícias, o comitê de agentes e executa ordens na Binance.
- **Postgres** (Vercel Postgres ou Neon) — fonte de verdade dos dados, compartilhada entre `bot/` e `web/`.
- **Upstash Redis** — pub/sub em tempo real, cache de leitura, fila de comandos do dashboard → bot, lock de ciclo.

Ver `arquitetura-tecnica.md` (projeto Claude) para o desenho completo.

## Setup rápido

### Bot (`bot/`)

```bash
cd bot
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # preencher com suas credenciais
python -m db.init_db  # cria as tabelas no Postgres
python main.py        # sobe o scheduler do ciclo de 15 min
```

Rodando como serviço Windows: ver `bot/service/README.md`.

### Dashboard (`web/`)

```bash
cd web
npm install
cp .env.example .env.local  # preencher com suas credenciais (mesmo DATABASE_URL e Redis do bot)
npm run dev
```

O schema do Postgres é criado pelo `python -m db.init_db` do lado do bot (fonte única do schema, ver seção acima) — rode-o antes de usar o dashboard pela primeira vez.

Criar o usuário de login do dashboard (senha, além do magic link):

```bash
cd web
DATABASE_URL=... node scripts/create-user.mjs ivanltds@gmail.com "sua-senha-aqui"
```

Deploy: qualquer push na `main` builda e publica automaticamente na Vercel (root path `web/`). Configurar na Vercel as mesmas variáveis de `.env.example` (Postgres, Redis, `SESSION_SECRET`, chave pública VAPID).

## Status (atualizado 16/09/2026)

**Bot PAUSADO (`bot_status = paused`) — não operar com capital real ainda.** Setup local concluído (credenciais reais, banco inicializado, dashboard funcional), mas a campanha de backtest multi-timeframe (`bot/run_backtest_multi_tf.py`) não validou vantagem estatística da estratégia atual: 10 de 12 testes (3 pares × 2 períodos × variações de stop) deram retorno negativo. Decisão: pausar e reconsiderar o desenho da estratégia antes de arriscar dinheiro real. Detalhes completos da campanha e o que falta revisar estão na seção 9 de `arquitetura-tecnica.md` (projeto Claude).

Scripts de backtest disponíveis em `bot/`:
- `run_backtest.py` — baseline single-timeframe (lib `backtesting.py`).
- `run_backtest_multi_tf.py SYMBOL [NUM_CANDLES] [DAYS_AGO] [STOP_ATR_MULT]` — simulador walk-forward fiel aos 3 timeframes reais, sem lookahead.

Pendências pra quando o trabalho for retomado (ver seção 9.3 de `arquitetura-tecnica.md` pra lista completa):

- Redesenho da lógica de confluência técnica (o filtro de ADX pra `mean_reversion` já foi implementado, mas não foi suficiente sozinho).
- Push pro repositório `github.com/ivanltds/ivanvestai` (aguardando PAT).
- `PortfolioAgent.run()` usando preço atual em vez de preço médio real via `get_my_trades()`.
- Arredondamento de quantidade por `LOT_SIZE`/`stepSize` no `ExecutionAgent`.
- Envio real do magic link (hoje só `console.log`).
- Teste dos schemas de Structured Outputs de cada agente contra a API da OpenAI de verdade.
- Classificação setorial e calendário macro (`bot/config/sector_map.json`, `bot/config/macro_calendar.json`) revisados.
