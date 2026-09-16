# IvanVestAI — Especificação do MVP (Bot de Trading Cripto com Comitê de Agentes)

Documento vivo com as decisões de escopo tomadas com o Ivan para o MVP. Atualizar conforme o projeto evolui.

## Visão geral

Bot Python que opera cripto (compra/venda) na Binance de forma automática, orientado por um comitê de agentes de IA, com um dashboard em Next.js para acompanhamento e configuração. Objetivo: lucrar com swing e day trade, apoiado em notícias, padrões técnicos e estratégias consolidadas.

## Decisões confirmadas

### Escopo e capital
- **Modo de operação**: direto com dinheiro real desde o início (sem paper trading em produção — mitigado por backtest obrigatório, ver seção de Estratégias).
- **Mercado**: Spot apenas. Futures está bloqueado para contas brasileiras (restrição CVM) — não será contornado por questões legais/ToS da Binance.
- **Capital inicial**: R$ 250, com aportes crescentes conforme confiança no sistema.
- **Conta Binance**: subconta dedicada só para o bot, isolando os fundos que ele gerencia do resto do patrimônio do Ivan na Binance.
- **Pares operados**: Top 100 por liquidez/market cap (universo de análise, não fixo — recalculado periodicamente).
- **Posições simultâneas**: sem limite fixo — o comitê decide dinamicamente com base em saldo e taxas.
- **Tamanho máximo por operação**: até 50% do capital disponível alocado numa única operação (mesmo sem limite de nº de posições simultâneas).
- **Stablecoin de segurança**: USDT.
- **Reserva de BNB**: sim, manter uma parte do capital em BNB para pagar taxas com desconto (0,1% → 0,075%).
- **Posições já existentes na conta (fora do bot)**: o bot assume gestão total delas (pode aplicar stop/take e vender) — na prática, isso se aplica ao que estiver dentro da subconta dedicada.

### Comitê de agentes
- **Provedor de LLM**: OpenAI (GPT).
- **Estratégia de modelos**: modelo barato (ex: gpt-4o-mini) para etapas simples (resumo de notícias, tradução, triagem), modelo mais robusto reservado para a decisão final de aprovar/executar operação.
- **Orçamento de API**: sem teto definido por enquanto — acompanhar custo real e revisar. Risco explícito: com ciclos a cada 15 min e múltiplos agentes em paralelo, o custo de API pode superar o capital de R$250 se não for monitorado de perto.
- **Estrutura do comitê**: não linear — agentes rodam em paralelo; se houver divergência entre eles, refazem a análise entre si para garantir integridade na decisão final (não é um pipeline sequencial simples).
- **Timeout de consenso**: prazo curto obrigatório apenas para decisões de **entrada** (abrir posição) — se estourar o prazo, a operação é cancelada por segurança. Para **saída/venda**, não há pressa — decisão pode ser mais deliberada.
- **Frequência do ciclo completo**: a cada 15 minutos.
- **Log de raciocínio**: completo e detalhado por agente (auditoria profunda), não só a decisão final.
- **Log de oportunidades rejeitadas**: sim, exibido no dashboard de forma resumida/colapsada.
- **Validação por backtest**: obrigatória antes de ligar o bot em produção — as estratégias/indicadores precisam ser testados contra dados históricos primeiro, já que não haverá paper trading em paralelo.
- **Volatilidade extrema (flash crash, notícia bombástica)**: o bot detecta movimento anormal de preço/volume e pausa automaticamente a abertura de novas posições até estabilizar, mas continua gerenciando (stop/take) as posições já abertas.
- **Nível mínimo de confiança para aprovar operação**: alto, ≥ 80%. Comitê deliberadamente seletivo — poucas operações, mas com mais convicção, dado o capital pequeno e a ausência de travas automáticas extras.

### Notícias
- Buscar no mínimo 10 notícias novas dos últimos 15 minutos via portais RSS.
- Lista de fontes RSS: Claude vai propor uma lista inicial (portais cripto confiáveis, incluindo fontes BR).
- Resumo e tradução para português: feito por modelo de LLM barato dedicado (não o modelo robusto do comitê).
- **Sentimento**: o agente de notícias gera um score numérico (-1 a +1) além do resumo qualitativo em texto — os outros agentes usam o score como input quantitativo, mas o texto fica registrado para auditoria.

### Estratégias de trading

- **Timeframes**: análise multi-timeframe — timeframes maiores (ex: 4h/1h) para identificar a tendência geral, timeframes menores (ex: 15m) para o timing preciso de entrada.
- **Tipos de estratégia habilitados** (o comitê escolhe qual aplicar conforme o contexto do ativo): trend following (seguir tendência), mean reversion (reversão à média), breakout (rompimento de faixa) e scalping de curtíssimo prazo.
- **Peso técnica vs notícia**: análise técnica é o filtro principal para gerar o sinal de entrada; notícias/sentimento servem como contexto — podem confirmar, reforçar ou vetar uma entrada (ex: evitar comprar em meio a notícia muito negativa), mas não substituem o sinal técnico.
- **Critério de entrada**: exigir confluência de pelo menos 2-3 indicadores técnicos concordando entre si antes de abrir posição (reduz sinais falsos).
- **Frameworks/indicadores específicos**: nenhuma preferência fechada — o comitê tem liberdade de escolher a técnica mais adequada por contexto (RSI, MACD, médias móveis, Bollinger Bands, volume, VWAP, etc. — Claude vai propor o conjunto inicial completo).
- **Trailing stop**: sim, usar trailing stop para proteger lucro acumulado conforme o preço evolui a favor da posição, além do stop/take inicial.
- **Correlação entre ativos**: evitar abrir múltiplas posições simultâneas em moedas altamente correlacionadas entre si (ex: várias altcoins que se movem junto com o BTC) — mitiga \"diversificação falsa\".
- **Diversificação por setor**: considerar a composição setorial do portfólio (DeFi, Layer 1, Layer 2, gaming, etc.) ao escolher entre oportunidades, evitando concentração excessiva num único setor.
- **Mercado lateral (sem tendência clara)**: o bot evita abrir novas posições e fica de fora até surgir uma tendência mais clara — preserva capital em períodos de indefinição.
- **Janelas de risco a evitar** (parâmetro configurável no dashboard, valores padrão sugeridos — a validar com dados reais de operação): evitar abrir novas posições durante janelas de anúncios macroeconômicos (ex: decisão do Fed, CPI dos EUA) e nos primeiros minutos após uma notícia bombástica. Sem restrição para fins de semana (mercado cripto não fecha como bolsa tradicional).
- **Critérios para tolerância de 5% em meme coins de alto risco** (múltiplos sinais, sem hierarquia fixa — o agente de notícias pondera com base no que encontrar): volume anormal de negociação (spike), menções/sentimento em redes sociais e notícias, e momentum de preço recente.
- **Swing vs day trade**: sem regra fixa de horizonte — o comitê decide dinamicamente o momento de saída de cada operação com base na análise, sem categorizar antecipadamente a operação como \"swing\" ou \"day trade\" (a classificação pode aparecer só como rótulo no relatório, após o fechamento).
- **Fonte de dados de mercado**: apenas Binance (API pública) para indicadores técnicos e para o backtest — consistente com onde as ordens são de fato executadas.

### Gestão de risco e execução
- **Stop-loss / take-profit**: decidido pelo agente caso a caso (não é uma regra fixa em %); reforçado por trailing stop (ver seção de Estratégias).
- **Circuit breaker diário**: dispara alerta por e-mail ao atingir **10% de perda do capital no dia** — não pausa o bot automaticamente (só notifica).
- **Trava extra de segurança**: nenhuma além do alerta acima (decisão explícita do Ivan, ciente do risco dado capital pequeno + tolerância a meme coins + execução 100% automática).
- **Tipo de ordem**: depende da liquidez do par — pares muito líquidos (BTC, ETH) usam ordem a mercado; pares menos líquidos usam limit com tolerância curta para evitar slippage alto.
- **Execução**: prioriza velocidade máxima na entrada (evitar perder timing). 100% automática, sem aprovação manual prévia.

### Flag de venda manual
- O usuário pode marcar uma posição para venda de duas formas configuráveis: (a) venda imediata a mercado, ou (b) agente escolhe o melhor momento/preço para otimizar a saída.
- Após vender via essa flag, o bot pode considerar recomprar automaticamente depois, se as condições voltarem a ser favoráveis (tratado como nova oportunidade pelo comitê).

### Dashboard (Next.js)
- **Ações manuais disponíveis**: pausar/retomar o bot (kill switch), forçar venda de uma posição, abrir posição manualmente, ajustar stop/take de uma posição aberta.
- **Autenticação**: sim, senha simples (mesmo sendo uso local).
- **Comunicação com o backend Python**: WebSocket em tempo real.
- **Moeda de exibição principal**: BRL (valores de saldo, lucro/perda apresentados em reais; exige buscar cotação USD/BRL em tempo real para conversão).
- **Balanço geral da carteira**: valor absoluto (BRL) + variação % do dia, sem comparação com baseline (BTC ou USDT parado) no MVP.
- **Exportação**: exportar histórico de operações em CSV/Excel (útil inclusive para declaração de IR de cripto).
- **Notificações externas**: e-mail (alertas de circuit breaker, operações, erros críticos).

### Infraestrutura e segurança
- **Execução do script Python**: roda como serviço em background com agendamento automático (não depende de terminal aberto); dashboard controla ligar/pausar.
- **Credenciais (API Key Binance, chave OpenAI)**: armazenadas em arquivo `.env` local, fora do controle de versão.
- **Segurança da API Key da Binance**: restrita por whitelist de IP fixo (o IP do PC do Ivan), além de permissão de trade habilitada.
- **Retenção de dados**: histórico de notícias, decisões dos agentes e logs mantido indefinidamente no MVP.

## Riscos e trade-offs explícitos (o Ivan optou conscientemente por aceitar)

1. **Sem paper trading em paralelo à produção** (mitigado parcialmente por backtest obrigatório antes de ligar) — bugs não capturados no backtest ainda podem impactar capital real desde o primeiro ciclo ao vivo.
2. **Custo de API sem teto** — rodando a cada 15 min com múltiplos agentes em paralelo, o gasto mensal com OpenAI pode se aproximar ou superar o capital de R$250 se não for monitorado ativamente.
3. **Capital muito pequeno (R$250)** frente aos valores mínimos de ordem e taxas da Binance — poucas operações já consomem uma fatia relevante do capital só em taxas (mitigado parcialmente pela reserva de BNB para desconto de taxa).
4. **Circuit breaker apenas informativo** — nenhuma trava automática além do e-mail de alerta em 10% de perda diária, mesmo com tolerância a meme coins de alto risco e execução 100% automática.
5. **IP whitelist na API Key** — se o IP público do Ivan mudar (ex: reset do roteador, troca de provedor), a API para de funcionar até atualizar a whitelist na Binance. Vale monitorar isso operacionalmente.
6. **Confiança mínima alta (≥80%) pode gerar poucas operações** — bom para seletividade, mas pode significar longos períodos sem nenhum trade, o que é esperado e não deve ser confundido com bot \"quebrado\".

Esses pontos não bloqueiam o desenvolvimento, mas devem ficar visíveis no dashboard (ex: contador de gasto de API do mês, contador de taxas pagas) para o Ivan acompanhar de perto.

## Próximos passos sugeridos
- Desenhar a arquitetura técnica (estrutura de agentes, banco de dados, backend FastAPI, comunicação WebSocket).
- Propor lista inicial de fontes RSS.
- Propor conjunto inicial de indicadores/estratégias técnicas (RSI, MACD, médias móveis, Bollinger Bands, volume, VWAP, etc.) e como cada tipo de estratégia (trend/mean reversion/breakout/scalping) usa esses indicadores.
- Definir schema de dados (operações, posições, notícias, decisões do comitê).
- Definir metodologia e período de dados para o backtest obrigatório pré-produção.
- Validar/ajustar os valores padrão de janelas de risco (anúncios macro, pós-notícia bombástica) com dados reais de operação.
