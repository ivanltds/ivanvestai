# Rodando o bot como serviço no Windows

O bot precisa ficar rodando em background, sobrevivendo a reinícios do
Windows, sem depender de um terminal aberto. Duas opções:

## Opção A — NSSM (Non-Sucking Service Manager, recomendado)

1. Baixe o NSSM: https://nssm.cc/download
2. Abra um terminal como Administrador na pasta onde extraiu o NSSM.
3. Rode:
   ```
   nssm install IvanVestAIBot
   ```
4. Na janela que abrir:
   - **Path**: caminho do `python.exe` dentro do `.venv` do projeto (ex: `C:\ivanvestai\bot\.venv\Scripts\python.exe`)
   - **Startup directory**: `C:\ivanvestai\bot`
   - **Arguments**: `main.py`
5. Na aba **Details**, dê um nome amigável (ex: "IvanVestAI Bot").
6. Na aba **I/O**, aponte stdout/stderr pra um arquivo de log (ex: `C:\ivanvestai\bot\logs\bot.log`) pra poder debugar depois.
7. Confirme. O serviço já fica registrado no Windows (`services.msc`), com início automático.

Para pausar o bot **não é necessário parar o serviço** — use o kill switch no dashboard (grava `bot_status = paused` no Postgres/fila do Redis). Pare o serviço via NSSM só se precisar atualizar o código.

## Opção B — Task Scheduler (mais simples, menos robusto)

1. Abra o **Agendador de Tarefas** do Windows.
2. Criar Tarefa Básica → dispare "Ao iniciar o computador".
3. Ação: iniciar um programa → aponte pro `python.exe` do `.venv`, com argumento `main.py` e "Iniciar em" apontando pra pasta `bot/`.
4. Em Configurações, marque "Reiniciar a tarefa se ela falhar" e defina algumas tentativas.

NSSM é preferível porque trata o processo como um serviço de verdade (reinicia sozinho se cair, loga melhor); Task Scheduler é mais simples de configurar mas menos resiliente a crashes do processo.
