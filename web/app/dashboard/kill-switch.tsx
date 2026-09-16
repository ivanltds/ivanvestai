"use client";

import { useState } from "react";

export default function KillSwitch() {
  const [busy, setBusy] = useState(false);

  async function send(command: "pause_bot" | "resume_bot") {
    setBusy(true);
    await fetch("/api/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command }),
    });
    setBusy(false);
  }

  return (
    <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
      <button className="danger" disabled={busy} onClick={() => send("pause_bot")}>
        Pausar bot
      </button>
      <button className="primary" disabled={busy} onClick={() => send("resume_bot")}>
        Retomar bot
      </button>
    </div>
  );
}
