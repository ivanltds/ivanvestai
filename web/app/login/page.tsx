"use client";

import { useState } from "react";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  async function handlePasswordLogin(e: React.FormEvent) {
    e.preventDefault();
    setStatus("Entrando...");
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (res.ok) {
      window.location.href = "/dashboard";
    } else if (res.status === 401) {
      setStatus("Senha incorreta.");
    } else {
      const data = await res.json().catch(() => null);
      setStatus(data?.error ?? "Erro ao entrar. Tente de novo.");
    }
  }

  async function handleMagicLink() {
    setStatus("Enviando link mágico...");
    const res = await fetch("/api/auth/magic-link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    setStatus(res.ok ? "Link enviado! Confira seu e-mail." : "Não foi possível enviar o link.");
  }

  return (
    <div className="card" style={{ maxWidth: 380, margin: "64px auto" }}>
      <h1 style={{ fontSize: 20 }}>IvanVestAI</h1>
      <p style={{ color: "var(--muted)", fontSize: 13 }}>
        Login com senha + confirmação por link mágico, já que o dashboard fica acessível pela internet.
      </p>
      <form onSubmit={handlePasswordLogin} style={{ display: "grid", gap: 8, marginTop: 16 }}>
        <input type="email" placeholder="e-mail" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <input type="password" placeholder="senha" value={password} onChange={(e) => setPassword(e.target.value)} required />
        <button type="submit" className="primary">Entrar com senha</button>
      </form>
      <button onClick={handleMagicLink} style={{ marginTop: 8, width: "100%" }}>
        Enviar link mágico por e-mail
      </button>
      {status && <p style={{ marginTop: 8, fontSize: 13 }}>{status}</p>}
    </div>
  );
}
