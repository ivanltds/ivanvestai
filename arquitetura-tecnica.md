# IvanVestAI — Arquitetura Técnica do MVP

Tradução das decisões de `mvp-spec.md` e `indicadores-estrategias.md` em desenho de sistema. Ponto de partida para a implementação.

## 0. Topologia geral (atualizado — bot local + dashboard na nuvem)

- **Dashboard (Next.js)**: hospedado na **Vercel**, no projeto já existente `ivanvestai` (`https://ivanvestai.vercel.app/`, repo `github.com/ivanltds/ivanvestai`, root path de deploy = pasta `web/`). Acessível de qualquer lugar (PC, celular).
- **Bot (Python)**: roda **local**, no PC do Ivan (Windows), 24/7 enquanto o PC estiver ligado e conectado. Fase 1 não exige uptime 24/7 garantido — se o PC estiver desligado/offline, o bot simplesmente não opera nesse período, e o dashboard deve refletir isso claramente (status "bot offline", não confundir com "pausado").
- **Ponte entre os dois**: **Upstash Redis** (free tier, já disponível via Vercel) fazendo (a) pub/sub em tempo real, (b) cache de leitura rápida, (c) fila de comandos do dashboard → bot, e (d) lock/coordenação entre ciclos.
- **Fonte de verdade dos dados**: **Postgres na nuvem** (Vercel Postgres ou Neon) — tanto o bot local quanto o dashboard na Vercel escrevem/leem do mesmo banco. Redis não guarda dado definitivo, só acelera/coordena.
- Repositório GitHub será reconstruído do zero com essa estrutura (ver seção 8) e configurado para deploy automático na Vercel a cada push na `main`.

## 1. Stack tecnológica

- **Backend (bot)**: Python 3.11+, **FastAPI** apenas para expor um health-check/API local mínima (o grosso da comunicação com o dashboard passa pelo Postgres + Redis, não por REST direto do bot).
- **Banco de dados**: **Postgres** (Vercel Postgres ou Neon — ambos têm free tier compatível com Vercel) via SQLAlchemy, com Alembic para migrations. Único banco para bot e dashboard.
- **Cache/mensageria**: **Upstash Redis** (REST API, compatível com ambiente serverless da Vercel e com Python via `upstash-redis` ou `redis-py` com TLS). Uso dimensionado para caber no free tier (ver seção 6).
- **Integração Binance**: `python-binance` (suporte direto a subconta), rodando só no processo local do bot — a Binance nunca é chamada direto do frontend/Vercel.
- **Indicadores técnicos**: `pandas-ta` sobre klines em `pandas.DataFrame`.
- **Backtest**: `backtesting.py` para validar as estratégias antes de ligar em produção.
- **Notícias**: `feedparser` para consumir os 10 RSS validados, deduplicação por similaridade de texto (`rapidfuzz`).
- **LLM**: SDK oficial da OpenAI (`openai` Python), usando **Structured Outputs** (JSON Schema) por agente.
- **Orquestração dos agentes**: `asyncio` puro — `asyncio.gather` para paralelismo, `asyncio.wait_for` para o timeout de entrada (45s).
- **Scheduler do ciclo de 15 min**: `APScheduler`, rodando dentro do processo Python local do bot.
- **Frontend**: Next.js (App Router) + TypeScript na pasta `web/`, hospedado na Vercel. Tempo real via **Server-Sent Events (SSE)** consumindo o Redis (mais simples que WebSocket num ambiente serverless).
- **Notificações**: e-mail via **Gmail SMTP com senha de app**; **push notification no navegador/celular** via Web Push API (VAPID) — dashboard pede permissão de notificação, backend (bot) dispara via `pywebpush` quando um alerta ocorre.
- **Autenticação do dashboard**: senha + camada extra (magic link por e-mail), já que fica exposto publicamente na internet.

## 2. Estrutura de pastas (monorepo, compatível com o repo/deploy atual da Vercel)

```
ivanvestai/
├── web/                               # Next.js — root path do deploy na Vercel
│   ├── app/
│   │   ├── dashboard/                 # balanço, posições, operações do dia
│   │   ├── news/                      # feed de notícias traduzidas
│   │   ├── settings/                  # tela de configuração de trading
│   │   ├── login/                     # senha + magic link
│   │   └── api/                       # rotas server-side (Next.js) que falam com Postgres/Redis
│   ├── components/
│   └── lib/
│       ├── sse-client.ts
│       └── push-client.ts
├── bot/                                # processo Python local
│   ├── agents/
│   │   ├── news_agent.py
│   │   ├── portfolio_agent.py
│   │   ├── market_scanner_agent.py
│   │   ├── viability_agent.py
│   │   ├── portfolio_comparison_agent.py
│   │   ├── risk_committee_agent.py    # decisão final, modelo robusto
│   │   └── execution_agent.py
│   ├── orchestrator/
│   │   ├── cycle_runner.py            # roda o ciclo de 15 min
│   │   └── reconciliation.py          # resolve divergência entre agentes
│   ├── core/
│   │   ├── binance_client.py
│   │   ├── indicators.py              # wrappers pandas-ta por estratégia
│   │   ├── backtest.py
│   │   ├── risk_rules.py              # circuit breaker, correlação, setor, timeout
│   │   ├── llm_client.py              # chamadas OpenAI (modelo barato vs robusto)
│   │   ├── redis_bridge.py            # publica eventos, lê fila de comandos, lock de ciclo
│   │   └── notifier.py                # e-mail + push
│   ├── db/
│   │   ├── models.py                  # SQLAlchemy models (compartilhado conceitualmente com web/)
│   │   └── alembic/
│   ├── config/
│   │   ├── settings.py                # lê .env local
│   │   ├── sector_map.json            # classificação setorial (v1 gerada por Claude)
│   │   └── macro_calendar.json        # eventos FOMC/CPI (v1 gerada por Claude)
│   ├── service/                       # empacotamento como serviço Windows (NSSM/Task Scheduler)
│   └── main.py
├── .env.local                         # segredos do bot (nunca commitado)
├── .github/workflows/                 # CI (lint/test) — deploy em si fica a cargo da integração nativa Vercel↔GitHub
└── README.md
```

## 3. Arquitetura do comitê de agentes

### 3.1 Papéis dos agentes (mapeados aos requisitos originais do MVP)

1. **NewsAgent** — busca RSS (10 fontes validadas), deduplica, resume, traduz PT-BR (modelo barato) e gera score de sentimento (-1 a +1).
2. **PortfolioAgent** — lê saldo/posições/preço médio via API da Binance (subconta dedicada), registra snapshot da carteira.
3. **MarketScannerAgent** — varre o Top 100 por liquidez, calcula indicadores multi-timeframe, classifica regime de mercado (tendência/lateral/rompimento) por ativo e propõe oportunidades com a estratégia mais adequada.
4. **ViabilityAgent** (agente de oscilação) — refina a análise das oportunidades do scanner, checa confluência de indicadores, aplica filtros de correlação/setor/volatilidade extrema, calcula confiança técnica.
5. **PortfolioComparisonAgent** — cruza carteira atual com oportunidades aprovadas, valida contra regras de capital (máx. 50%/operação, taxas, saldo de BNB, valor mínimo de ordem da Binance).
6. **RiskCommitteeAgent** (modelo robusto) — agrega os sinais de todos os agentes acima, calcula confiança final ponderada, aplica o veto (confiança < 80% = rejeitado), decide stop/take/trailing stop.
7. **ExecutionAgent** — executa a ordem (mercado ou limit conforme liquidez), registra a operação, gerencia trailing stop nas posições abertas, executa a flag de venda manual (imediata ou otimizada).

### 3.2 Orquestração por ciclo (a cada 15 min)

```
cycle_runner.py:
 1. redis_bridge adquire lock de ciclo (evita duas execuções simultâneas
    se o processo reiniciar no meio de um ciclo).
 2. Dispara em paralelo (asyncio.gather):
    NewsAgent, PortfolioAgent, MarketScannerAgent
 3. Com os resultados acima, dispara em paralelo:
    ViabilityAgent (por oportunidade) + PortfolioComparisonAgent
 4. Se houver divergência relevante entre ViabilityAgent e
    PortfolioComparisonAgent numa mesma oportunidade:
       -> reconciliation.py roda uma rodada extra, revotam uma vez.
 5. RiskCommitteeAgent recebe tudo consolidado, aplica o veto final.
 6. Timeout global dos passos 3-5 (decisão de ENTRADA): default 45s.
    Estourou -> operação cancelada por segurança.
    (Para SAÍDA/venda de posição aberta, não há esse timeout.)
 7. Aprovado -> ExecutionAgent executa e registra no Postgres.
    Rejeitado -> vai para o log de oportunidades rejeitadas.
 8. Cada evento relevante (posição atualizada, decisão tomada,
    notícia nova) é publicado no Redis (pub/sub) para o dashboard
    reagir via SSE quase em tempo real, e o cache de leitura rápida
    (últimas posições/decisões) é atualizado.
 9. Libera o lock de ciclo.
```

Cada chamada de agente grava seu racional completo (prompt + resposta estruturada) na tabela `agent_runs` do Postgres, vinculada ao ciclo e à oportunidade, para a auditoria detalhada que você pediu.

## 4. Modelo de dados (Postgres — tabelas principais)

- **wallet_snapshots**: timestamp, ativo, quantidade, preço médio de compra, valor atual, origem (bot/manual).
- **news_items**: timestamp, fonte, título original, resumo PT-BR, score de sentimento, url, hash de deduplicação.
- **opportunities**: timestamp do ciclo, par, estratégia sugerida, indicadores calculados (JSON), regime de mercado, status (aprovada/rejeitada), confiança final.
- **committee_decisions** (= `agent_runs`): opportunity_id, agente, decisão, confiança, justificativa completa, modelo usado, custo estimado da chamada.
- **orders / trades**: par, lado, tipo (mercado/limit), quantidade, preço, taxa paga, stop/take/trailing definidos, motivo, timestamp.
- **positions**: estado atual, preço médio, stop atual, take atual, trailing ativo, flag de venda (nenhuma/imediata/otimizada), flag de recompra habilitada.
- **daily_equity**: snapshot diário de equity total em BRL, usado pro circuit breaker de 10%.
- **api_cost_log**: chamada de LLM, modelo usado, tokens, custo estimado — alimenta o contador de gasto de API no dashboard.
- **settings**: chave/valor da tela de configuração (stablecoin de segurança, teto por operação, thresholds, janelas de risco, status do bot, etc.).
- **users**: login do dashboard (usuário + hash de senha + estado de magic link).
- **push_subscriptions**: endpoints de push notification registrados pelo navegador/celular do Ivan.

## 5. Papel do Redis (Upstash) e dimensionamento pro free tier

| Uso | Como | Cuidado com o limite gratuito |
|---|---|---|
| Pub/Sub tempo real | Bot publica em canais (`positions`, `decisions`, `news`, `alerts`) após cada ciclo/evento | Publicar só eventos agregados por ciclo (não por cálculo interno), ~1 msg por canal a cada 15 min + eventos pontuais de execução |
| Cache de leitura | Chaves tipo `cache:positions`, `cache:balance`, `cache:last_decisions` com TTL curto | Poucas chaves, tamanho pequeno (JSON compacto), evita reprocessar Postgres a cada carregamento do dashboard |
| Fila de comandos | Lista/stream `commands:bot` — dashboard grava comando, bot consome no início do próximo ciclo (ou via polling curto para ações urgentes tipo kill switch) | Baixíssimo volume (ações manuais são raras) |
| Lock de ciclo | Chave `lock:cycle` com TTL um pouco maior que o timeout do ciclo | 1 operação de SET/DEL por ciclo |

Com ciclo de 15 min (96/dia) + eventos pontuais, o volume de comandos fica bem abaixo dos limites do free tier do Upstash — não deve ser necessário upgrade no MVP.

## 6. API e tempo real

- **Rotas server-side do Next.js** (`web/app/api/...`): autenticação (senha + magic link), configurações de trading, exportação CSV/Excel, histórico paginado (lendo Postgres), envio de comandos (grava na fila do Redis que o bot consome).
- **SSE** (`web/app/api/stream`): assina os canais do Redis e repassa pro navegador em tempo real — atualização de posição, nova decisão do comitê (inclusive rejeitadas, resumidas), novo item de notícia, alerta de circuit breaker/volatilidade, status do bot.
- **Push notifications**: registro de subscription no navegador (Web Push/VAPID), backend do bot dispara via `pywebpush` em alertas críticos (circuit breaker, erro, flash crash).
- Autenticação de sessão via cookie assinado (JWT ou sessão do Next Auth), tanto nas rotas REST quanto no stream SSE.

## 7. Execução local e segurança

- Bot roda como serviço em **background no Windows** (NSSM ou Task Scheduler) chamando `bot/main.py`, que sobe o `APScheduler` e a ponte com Redis/Postgres.
- Dashboard liga/pausa o bot via `settings.bot_status` no Postgres (ou comando na fila do Redis) — `cycle_runner` lê no início de cada ciclo.
- Credenciais do bot em `.env.local` (API Key Binance da subconta, chave OpenAI, Postgres, Redis, credenciais Gmail SMTP, chaves VAPID) — nunca versionado. Credenciais do frontend (Postgres, Redis, VAPID pública) configuradas direto no painel da Vercel; sincronização entre os dois ambientes é manual por enquanto.
- API Key da Binance restrita por whitelist de IP fixo no painel da Binance (passo manual, fora do código).
- Backtest obrigatório: `bot/core/backtest.py` roda contra klines históricas da Binance antes de qualquer liberação para produção; resultado fica registrado no Postgres e deve ser revisado manualmente antes de ligar `bot_status = running`.

## 8. Repositório e deploy

- O repositório `github.com/ivanltds/ivanvestai` será **reconstruído do zero** com a estrutura da seção 2 (nada do conteúdo atual é reaproveitado).
- Root path de deploy na Vercel continua `web/` (já configurado no projeto existente).
- **Deploy automático**: integração nativa GitHub↔Vercel já cuida disso — push na `main` builda e publica o `web/` automaticamente. Não é necessário workflow de deploy customizado; um `.github/workflows/` simples de lint/test é suficiente do lado de CI.
- Acesso ao repositório: Ivan vai gerar um Personal Access Token fine-grained (escopo só nesse repo, permissão Contents read/write) para o Claude usar nesta sessão e fazer o push. Aguardando o token.

## 9. Status da implementação (atualizado 16/09/2026)

Scaffold completo gerado e validado no ambiente do Claude: `bot/` (Python) e `web/` (Next.js) com a estrutura da seção 2, incluindo os 7 agentes, orquestração (`cycle_runner` + `reconciliation`), modelo de dados completo (SQLAlchemy), ponte com Redis, notificações (e-mail + push), backtest baseline, dashboard funcional (login com senha+magic link, posições, operações do dia, kill switch, notícias, configurações, export CSV, SSE em tempo real), `sector_map.json` e `macro_calendar.json` v1 (datas de FOMC/CPI 2026 verificadas nas fontes oficiais: federalreserve.gov e BLS). Build do `web/` passa limpo (`npm run build`, `tsc --noEmit`), bot passa `ruff check` sem erros. Entregue como `ivanvestai-scaffold.zip` ao Ivan (via chat).

**Ainda não feito** (é um scaffold validado estaticamente, não um sistema testado em produção):
- Nenhum código foi testado contra credenciais reais (Binance, OpenAI, Postgres, Redis, Gmail, VAPID) — só validação estática (compila/builda/lint), sem execução ponta a ponta.
- Push pro repositório `github.com/ivanltds/ivanvestai` ainda não feito — aguardando o PAT do Ivan (ver seção 8).
- Provedor exato de Postgres (Vercel Postgres vs. Neon) a decidir no momento de provisionar.
- Backtest ainda não rodado de verdade (precisa de credenciais Binance + revisão manual dos resultados antes de `bot_status = running`; o código começa com `bot_status = paused` por padrão, de propósito).
- `PortfolioAgent.run()` usa o preço atual como aproximação do preço médio de compra — falta puxar o histórico real via `get_my_trades()` da Binance pra calcular o preço médio de verdade.
- `ExecutionAgent`/quantidade da ordem: a conversão de valor sugerido (USDT) para quantidade ainda é simplificada — falta arredondar pelo `stepSize`/`LOT_SIZE` do símbolo antes de enviar a ordem de verdade.
- Envio do e-mail do magic link ainda é só um placeholder (log) no código — falta plugar um provedor de fato.
- Especificar/testar o schema JSON (Structured Outputs) de cada agente contra a API da OpenAI de verdade (só foi validado que o código compila, não que os prompts produzem saídas de boa qualidade).
