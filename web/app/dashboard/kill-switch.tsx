"use client";

// Kill switch (pausar/retomar o bot). Antes mostrava os dois botões sempre
// visíveis, sem nenhuma indicação do status atual nem confirmação de que o
// comando realmente surtiu efeito -- só um "disabled" rápido enquanto o
// fetch rodava. A pedido do Ivan: só o botão da ação que FAZ SENTIDO no
// estado atual fica visível (se está rodando, só "Pausar"; se está pausado,
// só "Retomar"), com um selo de status sempre visível e feedback explícito
// em cada etapa -- incluindo o caso real de "o comando foi enfileirado mas
// ninguém consumiu" (bot local desligado), que já aconteceu nesta sessão.
import { useEffect, useRef, useState } from "react";

type BotStatus = "running" | "paused";
type Phase = "idle" | "sending" | "waiting" | "confirmed" | "timeout" | "error";

const POLL_INTERVAL_MS = 3000;
const POLL_TIMEOUT_MS = 30000; // ~2x o intervalo de drain_commands do bot (15s)

async function fetchStatus(): Promise<BotStatus | null> {
  try {
    const res = await fetch("/api/bot-status", { cache: "no-store" });
    if (!res.ok) return null;
    const data = await res.json();
    return data.status === "running" ? "running" : "paused";
  } catch {
    return null;
  }
}

export default function KillSwitch({ initialStatus }: { initialStatus: BotStatus }) {
  const [status, setStatus] = useState<BotStatus>(initialStatus);
  const [phase, setPhase] = useState<Phase>("idle");
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollDeadline = useRef<number>(0);

  useEffect(() => {
    // Confere o status de verdade ao carregar a página (o valor inicial veio
    // do server component, mas pode estar levemente desatualizado em cache).
    fetchStatus().then((s) => {
      if (s) setStatus(s);
    });
    return () => stopPolling();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function stopPolling() {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }

  function startPolling(expected: BotStatus) {
    stopPolling();
    pollDeadline.current = Date.now() + POLL_TIMEOUT_MS;
    pollTimer.current = setInterval(async () => {
      const s = await fetchStatus();
      if (s === expected) {
        setStatus(s);
        setPhase("confirmed");
        stopPolling();
        setTimeout(() => setPhase("idle"), 2500);
        return;
      }
      if (Date.now() > pollDeadline.current) {
        stopPolling();
        setPhase("timeout");
      }
    }, POLL_INTERVAL_MS);
  }

  async function send(command: "pause_bot" | "resume_bot") {
    const expected: BotStatus = command === "pause_bot" ? "paused" : "running";
    setPhase("sending");
    try {
      const res = await fetch("/api/commands", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command }),
      });
      if (!res.ok) throw new Error("falha ao enviar");
      setPhase("waiting");
      startPolling(expected);
    } catch {
      setPhase("error");
    }
  }

  const busy = phase === "sending" || phase === "waiting";
  const isRunning = status === "running";

  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span
          style={{
            display: "inline-block",
            width: 10,
            height: 10,
            borderRadius: "50%",
            background: isRunning ? "var(--green)" : "var(--muted)",
          }}
        />
        <span style={{ fontWeight: 600 }}>{isRunning ? "Bot rodando" : "Bot pausado"}</span>
      </div>

      <div style={{ marginTop: 10 }}>
        {isRunning ? (
          <button className="danger" disabled={busy} onClick={() => send("pause_bot")}>
            Pausar bot
          </button>
        ) : (
          <button className="primary" disabled={busy} onClick={() => send("resume_bot")}>
            Retomar bot
          </button>
        )}
      </div>

      <div style={{ marginTop: 6, fontSize: 12, minHeight: 16 }}>
        {phase === "sending" && <span style={{ color: "var(--muted)" }}>Enviando comando...</span>}
        {phase === "waiting" && (
          <span style={{ color: "var(--muted)" }}>
            Comando enviado -- aguardando o bot confirmar (o processo local consome a fila a cada ~15s)...
          </span>
        )}
        {phase === "confirmed" && (
          <span style={{ color: "var(--green)" }}>
            Confirmado: {isRunning ? "bot está rodando." : "bot está pausado."}
          </span>
        )}
        {phase === "timeout" && (
          <span style={{ color: "var(--red)" }}>
            O bot não confirmou o comando em 30s. Confira se o processo <code>python main.py</code> está
            rodando na sua máquina -- sem ele, nada consome a fila de comandos.
          </span>
        )}
        {phase === "error" && (
          <span style={{ color: "var(--red)" }}>Falha ao enviar o comando -- tenta de novo.</span>
        )}
      </div>
    </div>
  );
}
