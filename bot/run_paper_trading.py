"""Paper trading (dry-run) ao vivo, em paralelo, das 4 configurações de
estratégia já testadas em backtest nesta sessão: comitê redesenhado
(trend_following/breakout com gate de ADX, mean_reversion desativado),
Kotegawa (reversão à média) e RAPF com e sem filtros de força/volume.

Por quê isso além dos backtests: os backtests já rodados (ver seção 9.5 de
arquitetura-tecnica.md) são walk-forward sem lookahead, mas ainda são sobre
dados HISTÓRICOS -- sempre existe o risco (pequeno, mas real) de algum detalhe
sutil de implementação ter vazado informação do futuro sem a gente perceber.
Rodar a MESMA lógica contra dados ao vivo da Binance, candle a candle, à
medida que eles realmente acontecem, é o teste out-of-sample mais rigoroso
possível -- literalmente impossível de ter lookahead. O preço a pagar é a
amostra: uma noite só gera poucos trades (provavelmente < 10 no total pra
todas as estratégias/pares somados), então isso NÃO decide nada sozinho --
é (a) uma checagem de que o código roda certo contra a API ao vivo (antes de
cogitar operar de verdade) e (b) mais alguns pontos de dado genuinamente
out-of-sample pra somar à análise já feita.

100% DRY-RUN: este script só faz chamadas de LEITURA na Binance
(get_klines_df, get_last_price) -- nunca chama place_market_order nem
place_limit_order. As "posições" abertas/fechadas aqui são inteiramente
simuladas em memória e no arquivo de estado local; nenhuma ordem real é
enviada, nenhum saldo real é tocado.

Reaproveita a lógica (já validada/discutida) de:
  - run_backtest_multi_tf.py   -> config "committee"
  - run_backtest_kotegawa.py   -> config "kotegawa"
  - run_backtest_rapf.py       -> configs "rapf_filtros" / "rapf_sem_filtros"
em vez de duplicar as regras, pra garantir que o que roda ao vivo é
exatamente o que foi validado no backtest.

Gestão de posição (stop/take) usa o preço "ao vivo" (ticker) a cada ciclo, em
vez de esperar o fechamento do próximo candle -- mais realista (mais perto de
como o ExecutionAgent real reagiria), ao custo de não capturar o pavio exato
de um candle que tocou o stop/take entre um ciclo e outro (aproximação
aceitável pra um dry-run).

Uso:
    python run_paper_trading.py [LOOP_SECONDS] [SYMBOLS_CSV]

    python run_paper_trading.py                     # ciclo de 5 min, 8 pares padrão
    python run_paper_trading.py 300 BTCUSDT,ETHUSDT  # só 2 pares

Deixa rodando num terminal (Ctrl+C pra parar -- imprime um resumo final).
Grava cada entrada/saída em paper_trades.csv (append, fonte de verdade local)
E na tabela `paper_trades` do Postgres compartilhado (best-effort -- se
cair, só avisa e segue com o CSV), pra alimentar o dashboard web em
/paper-trading. Precisa rodar `python -m db.init_db` uma vez antes (cria a
tabela nova -- não mexe nas existentes). O estado (posições simuladas
abertas) fica em paper_trading_state.json (sobrescrito a cada ciclo, pra
sobreviver a um restart do script sem perder o que já estava aberto).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys
import time

import pandas as pd

import pandas_ta as ta

from core import vlog
from core.binance_client import binance_client
from core.indicators import (
    _col,
    adx_last,
    atr_stop_reference,
    confluence_score,
    score_breakout,
    score_trend_following,
)
from db.models import PaperTrade
from db.session import get_session
from run_backtest_kotegawa import BB_LENGTH, _confirms
from run_backtest_multi_tf import LOOKBACK as COMMITTEE_LOOKBACK
from run_backtest_multi_tf import REWARD_RISK_RATIO as COMMITTEE_REWARD_RISK
from run_backtest_rapf import (
    LOOKBACK_AVG,
    STRENGTH_MULT,
    VOLUME_MULT,
    _add_emas,
    _big_trend,
)

SCRIPT_VERSION = "2026-09-16-v3-vlog"

SYMBOLS_DEFAULT = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT",
]

STRATEGY_CONFIGS = [
    {"name": "committee", "kind": "committee"},
    {"name": "kotegawa", "kind": "kotegawa"},
    {"name": "rapf_filtros", "kind": "rapf", "require_filters": True},
    {"name": "rapf_sem_filtros", "kind": "rapf", "require_filters": False},
]

COMMITTEE_STOP_ATR_MULT = 1.5  # config base validada (ver arquitetura-tecnica.md 9.5) -- não a variante de stop largo
KOTEGAWA_INTERVAL = "1h"  # fonte recomenda 1d, mas numa noite só isso daria ~0 sinais -- 1h dá amostra viável
KOTEGAWA_BB_STD = 3.0
RAPF_BIG_INTERVAL = "4h"
RAPF_SMALL_INTERVAL = "1h"
RAPF_REWARD_RISK = 2.5

STATE_PATH = "paper_trading_state.json"
CSV_PATH = "paper_trades.csv"
CSV_FIELDS = ["timestamp_utc", "strategy", "symbol", "event", "direction", "price", "reason", "pnl_pct", "extra"]

_INTERVAL_DELTA = {
    "15m": pd.Timedelta(minutes=15),
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
}


def _drop_unclosed(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Mesma lógica de _closed_before dos backtests, mas contra o relógio real
    (não um `cutoff` simulado) -- garante que nunca usamos o candle que ainda
    está se formando."""
    now = pd.Timestamp.now(tz="UTC")
    delta = _INTERVAL_DELTA[interval]
    return df[df["open_time"] + delta <= now]


# --- estado -----------------------------------------------------------------

def default_state() -> dict:
    state: dict = {}
    for cfg in STRATEGY_CONFIGS:
        state[cfg["name"]] = {}
        for symbol in SYMBOLS:
            entry: dict = {"position": None, "last_bar": None}
            if cfg["kind"] == "kotegawa":
                entry["pending_alert"] = None
            elif cfg["kind"] == "rapf":
                entry.update({"state": "idle", "context_dir": None, "min_low_since_lost": None, "trigger": None})
            state[cfg["name"]][symbol] = entry
    return state


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return default_state()
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        fresh = default_state()
        for cfg in STRATEGY_CONFIGS:
            name = cfg["name"]
            for symbol in SYMBOLS:
                if name in saved and symbol in saved[name]:
                    fresh[name][symbol].update(saved[name][symbol])
        print(f"Estado anterior carregado de {STATE_PATH}.")
        return fresh
    except Exception as e:
        print(f"[aviso] não consegui carregar estado anterior ({e}) -- começando do zero.")
        return default_state()


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)


def log_trade(strategy: str, symbol: str, event: str, direction: str, price: float,
              reason: str | None, pnl_pct: float | None, extra: dict | None) -> None:
    """Grava o evento (entrada/saída simulada) em dois lugares: o CSV local
    (fonte de verdade, sempre grava, nunca falha por causa de rede) e o
    Postgres compartilhado (tabela `paper_trades`), pra alimentar o dashboard
    web em /paper-trading quase em tempo real. A gravação no Postgres é
    best-effort -- se a rede cair ou a tabela ainda não existir (precisa
    rodar `python -m db.init_db` uma vez), só avisa e segue rodando; o CSV
    continua sendo a fonte confiável pra análise offline de qualquer forma."""
    is_new = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "strategy": strategy, "symbol": symbol, "event": event, "direction": direction,
            "price": f"{price:.8f}", "reason": reason or "",
            "pnl_pct": f"{pnl_pct:.4f}" if pnl_pct is not None else "",
            "extra": json.dumps(extra or {}),
        })

    try:
        with get_session() as session:
            session.add(PaperTrade(
                strategy=strategy, symbol=symbol, event=event, direction=direction,
                price=price, reason=reason, pnl_pct=pnl_pct, extra_json=extra or {},
            ))
    except Exception as e:
        print(f"  [aviso] não gravei no Postgres ({e}) -- CSV local seguiu normal, dashboard fica desatualizado até a próxima gravação")


# --- gestão de posição aberta (comum às 4 configs) ---------------------------

def manage_open_position(strategy: str, symbol: str, price: float, state: dict) -> None:
    sym_state = state[strategy][symbol]
    pos = sym_state["position"]
    if pos is None:
        return
    direction = pos["direction"]
    exit_price = reason = None
    if direction == "long":
        if price <= pos["stop_price"]:
            exit_price, reason = price, "stop_loss"
        elif price >= pos["take_price"]:
            exit_price, reason = price, "take_profit"
    if exit_price is None:
        return
    pnl_pct = (exit_price / pos["entry_price"] - 1) * 100
    log_trade(strategy, symbol, "exit", direction, exit_price, reason, pnl_pct, pos.get("extra"))
    reason_emoji = {"stop_loss": "🛑", "take_profit": "🎉"}.get(reason, "🚪")
    vlog.exit_(
        f"{strategy:18s} {symbol:9s} {reason_emoji} {reason:12s} preço={exit_price:.4f} pnl={pnl_pct:+.2f}%",
        positive=pnl_pct >= 0,
    )
    sym_state["position"] = None


def open_position(strategy: str, symbol: str, signal: dict, state: dict) -> None:
    sym_state = state[strategy][symbol]
    sym_state["position"] = {
        "direction": signal["direction"], "entry_price": signal["entry_price"],
        "stop_price": signal["stop_price"], "take_price": signal["take_price"],
        "entry_time": dt.datetime.now(dt.timezone.utc).isoformat(), "extra": signal.get("extra", {}),
    }
    log_trade(strategy, symbol, "entry", signal["direction"], signal["entry_price"], None, None, signal.get("extra"))
    extra_str = ", ".join(f"{k}={v}" for k, v in (signal.get("extra") or {}).items())
    vlog.entry(
        f"{strategy:18s} {symbol:9s} preço={signal['entry_price']:.4f} "
        f"stop={signal['stop_price']:.4f} take={signal['take_price']:.4f} ({extra_str})"
    )


# --- avaliação de entrada por tipo de estratégia -----------------------------

def _committee_entry(symbol: str, sym_state: dict, df_15m, df_1h, df_4h) -> dict | None:
    if len(df_15m) < 60 or len(df_1h) < 55 or len(df_4h) < 20:
        return None
    last_bar = str(df_15m["open_time"].iloc[-1])
    if sym_state.get("last_bar") == last_bar:
        return None
    sym_state["last_bar"] = last_bar

    window_15m = df_15m.tail(COMMITTEE_LOOKBACK)
    window_1h = df_1h.tail(COMMITTEE_LOOKBACK)
    window_4h = df_4h.tail(COMMITTEE_LOOKBACK)

    adx_4h_val = adx_last(window_4h)
    votes: list = []
    strategy = ""
    if adx_4h_val > 25:
        votes = score_trend_following(window_1h, window_15m)
        strategy = "trend_following"
        breakout_votes = score_breakout(window_15m)
        if confluence_score(breakout_votes) >= 2:
            votes = breakout_votes
            strategy = "breakout"

    score = confluence_score(votes) if votes else 0
    if not strategy or score < 2:
        return None

    price = float(df_15m["close"].iloc[-1])
    atr_stop = atr_stop_reference(window_15m, multiplier=COMMITTEE_STOP_ATR_MULT)
    return {
        "direction": "long", "entry_price": price,
        "stop_price": price - atr_stop, "take_price": price + atr_stop * COMMITTEE_REWARD_RISK,
        "extra": {"sub_strategy": strategy, "adx_4h": round(adx_4h_val, 1), "score": score},
    }


def _kotegawa_entry(symbol: str, sym_state: dict, df_1h) -> dict | None:
    df = df_1h
    if len(df) < BB_LENGTH + 5:
        return None
    last_bar = str(df["open_time"].iloc[-1])
    if sym_state.get("last_bar") == last_bar:
        return None
    sym_state["last_bar"] = last_bar

    df = df.copy()
    bb = ta.bbands(df["close"], length=BB_LENGTH, std=KOTEGAWA_BB_STD)
    df["bb_lower"] = _col(bb, "BBL_")
    df["bb_mid"] = _col(bb, "BBM_")
    df["bb_upper"] = _col(bb, "BBU_")
    if pd.isna(df["bb_mid"].iloc[-1]):
        return None

    i = len(df) - 1
    price = float(df["close"].iloc[i])
    signal = None

    pending = sym_state.get("pending_alert")
    if pending is not None:
        direction = pending["direction"]
        if direction == "long" and _confirms(df, i, "long"):
            signal = {
                "direction": "long", "entry_price": price,
                "stop_price": pending["stop_ref"], "take_price": float(df["bb_mid"].iloc[i]),
                "extra": {"kotegawa_direction": "long"},
            }
        # confirmação de alerta "short" é só informativa (spot-only) -- não abre posição real
        sym_state["pending_alert"] = None

    if signal is None and sym_state.get("pending_alert") is None:
        if price < float(df["bb_lower"].iloc[i]):
            sym_state["pending_alert"] = {"direction": "long", "stop_ref": float(df["low"].iloc[i])}
        elif price > float(df["bb_upper"].iloc[i]):
            sym_state["pending_alert"] = {"direction": "short", "stop_ref": float(df["high"].iloc[i])}

    return signal


def _rapf_entry(symbol: str, sym_state: dict, df_1h, df_4h, require_filters: bool) -> dict | None:
    df_small, df_big = df_1h, df_4h
    if len(df_small) < 60 or len(df_big) < 55:
        return None
    last_bar = str(df_small["open_time"].iloc[-1])
    if sym_state.get("last_bar") == last_bar:
        return None
    sym_state["last_bar"] = last_bar

    df_small = _add_emas(df_small)
    df_big = _add_emas(df_big)
    row = df_small.iloc[-1]
    if pd.isna(row["ema50"]):
        return None
    price, high, low = float(row["close"]), float(row["high"]), float(row["low"])
    big_trend = _big_trend(df_big.iloc[-1])

    state = sym_state.get("state", "idle")
    context_dir = sym_state.get("context_dir")
    min_low = sym_state.get("min_low_since_lost")
    trigger = sym_state.get("trigger")

    if state != "idle" and big_trend != context_dir:
        state, context_dir, trigger, min_low = "idle", None, None, None

    small_dir = None
    if row["ema9"] > row["ema21"] > row["ema50"]:
        small_dir = "up"
    elif row["ema9"] < row["ema21"] < row["ema50"]:
        small_dir = "down"

    signal = None

    if state == "idle":
        if big_trend in ("up", "down") and small_dir == big_trend:
            state, context_dir = "aligned", big_trend

    elif state == "aligned":
        lost = (context_dir == "up" and price < row["ema50"]) or \
               (context_dir == "down" and price > row["ema50"])
        if lost:
            state = "lost_fluidity"
            min_low = low if context_dir == "up" else high

    elif state == "lost_fluidity":
        min_low = (min(min_low, low) if context_dir == "up" else max(min_low, high))
        reclaimed = (context_dir == "up" and price > row["ema21"] and price > row["open"]) or \
                    (context_dir == "down" and price < row["ema21"] and price < row["open"])
        if reclaimed:
            ok = True
            if require_filters:
                recent = df_small.iloc[max(0, len(df_small) - 1 - LOOKBACK_AVG):len(df_small) - 1]
                avg_range = (recent["high"] - recent["low"]).mean()
                avg_vol = recent["volume"].mean()
                candle_range = high - low
                ok = (avg_range > 0 and candle_range >= STRENGTH_MULT * avg_range and
                      avg_vol > 0 and row["volume"] >= VOLUME_MULT * avg_vol)
            if ok:
                stop_ref = min(low, min_low) if context_dir == "up" else max(high, min_low)
                trigger = {"high": high, "low": low, "stop_ref": stop_ref}
                state = "armed"

    elif state == "armed":
        if context_dir == "up":
            if high >= trigger["high"]:
                entry_price, stop_price = trigger["high"], trigger["stop_ref"]
                risk = entry_price - stop_price
                if risk > 0:
                    signal = {
                        "direction": "long", "entry_price": entry_price, "stop_price": stop_price,
                        "take_price": entry_price + risk * RAPF_REWARD_RISK,
                        "extra": {"rapf_context": context_dir},
                    }
                state, context_dir, trigger, min_low = "idle", None, None, None
            elif low <= trigger["stop_ref"]:
                state, context_dir, trigger, min_low = "idle", None, None, None
        else:
            # tendência de baixa -- setup de venda, informativo apenas (spot-only, não entra de verdade)
            if low <= trigger["low"] or high >= trigger["stop_ref"]:
                state, context_dir, trigger, min_low = "idle", None, None, None

    sym_state.update({"state": state, "context_dir": context_dir, "min_low_since_lost": min_low, "trigger": trigger})
    return signal


def evaluate_entry(cfg: dict, symbol: str, sym_state: dict, df_15m, df_1h, df_4h) -> dict | None:
    if cfg["kind"] == "committee":
        return _committee_entry(symbol, sym_state, df_15m, df_1h, df_4h)
    if cfg["kind"] == "kotegawa":
        return _kotegawa_entry(symbol, sym_state, df_1h)
    if cfg["kind"] == "rapf":
        return _rapf_entry(symbol, sym_state, df_1h, df_4h, cfg["require_filters"])
    raise ValueError(f"kind desconhecido: {cfg['kind']}")


# --- ciclo principal ----------------------------------------------------------

def tick(state: dict, counters: dict) -> None:
    now_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    counters["ticks"] = counters.get("ticks", 0) + 1
    vlog.section(f"Ciclo @ {now_str}", emoji="🔄")
    if counters["ticks"] % 12 == 0:  # a cada ~1h (ciclo de 5min) uma piadinha, sem exagero
        vlog.joke()
    any_event = False

    for symbol in SYMBOLS:
        try:
            price = binance_client.get_last_price(symbol)
        except Exception as e:
            vlog.fail(f"{symbol}: erro ao buscar preço ({e}), pulando símbolo neste ciclo")
            continue

        for cfg in STRATEGY_CONFIGS:
            before = state[cfg["name"]][symbol]["position"]
            manage_open_position(cfg["name"], symbol, price, state)
            if before is not None and state[cfg["name"]][symbol]["position"] is None:
                any_event = True
                counters["exits"] += 1

        any_flat = any(state[cfg["name"]][symbol]["position"] is None for cfg in STRATEGY_CONFIGS)
        if not any_flat:
            continue

        try:
            df_15m = _drop_unclosed(binance_client.get_klines_df(symbol, "15m", 150), "15m")
            df_1h = _drop_unclosed(binance_client.get_klines_df(symbol, "1h", 150), "1h")
            df_4h = _drop_unclosed(binance_client.get_klines_df(symbol, "4h", 150), "4h")
        except Exception as e:
            vlog.fail(f"{symbol}: erro ao buscar candles ({e}), pulando avaliação de entrada")
            continue

        for cfg in STRATEGY_CONFIGS:
            name = cfg["name"]
            sym_state = state[name][symbol]
            if sym_state["position"] is not None:
                continue
            try:
                signal = evaluate_entry(cfg, symbol, sym_state, df_15m, df_1h, df_4h)
            except Exception as e:
                vlog.fail(f"{symbol}/{name}: erro ao avaliar entrada ({e})")
                continue
            if signal:
                open_position(name, symbol, signal, state)
                any_event = True
                counters["entries"] += 1

    if not any_event:
        open_now = sum(
            1 for cfg in STRATEGY_CONFIGS for s in SYMBOLS if state[cfg["name"]][s]["position"] is not None
        )
        vlog.step(
            "😴", "Sem novidade",
            f"nenhuma entrada/saída neste ciclo -- {open_now} posição(ões) simulada(s) aberta(s), "
            f"{counters['entries']} entradas e {counters['exits']} saídas desde o início",
        )


def main() -> None:
    global SYMBOLS
    loop_seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    SYMBOLS = sys.argv[2].split(",") if len(sys.argv) > 2 else SYMBOLS_DEFAULT

    vlog.banner("🧪  PAPER TRADING AO VIVO", f"versão {SCRIPT_VERSION}", color="green")
    vlog.joke()
    print(f"  Pares:              {', '.join(SYMBOLS)}")
    print(f"  Estratégias:        {', '.join(c['name'] for c in STRATEGY_CONFIGS)}")
    print(f"  Ciclo:              a cada {loop_seconds}s")
    print("  Modo:               100% leitura -- NENHUMA ordem real é enviada à Binance.")
    print(f"  Log de trades:      {CSV_PATH} (append, fonte de verdade local)")
    print("                      + tabela `paper_trades` no Postgres (best-effort, alimenta /paper-trading no dashboard)")
    print(f"  Estado:             {STATE_PATH} (sobrescrito a cada ciclo)")
    print("  Ctrl+C pra parar (imprime um resumo antes de sair).")

    state = load_state()
    counters = {"entries": 0, "exits": 0, "ticks": 0}

    try:
        while True:
            try:
                tick(state, counters)
            except Exception as e:
                vlog.fail(f"Erro no ciclo, continuando no próximo: {e}")
            save_state(state)
            time.sleep(loop_seconds)
    except KeyboardInterrupt:
        open_positions = [
            (cfg["name"], s) for cfg in STRATEGY_CONFIGS for s in SYMBOLS
            if state[cfg["name"]][s]["position"] is not None
        ]
        vlog.banner("🛑  INTERROMPIDO PELO USUÁRIO", "resumo da sessão", color="yellow")
        vlog.money(f"Entradas simuladas: {counters['entries']}  |  Saídas simuladas: {counters['exits']}")
        vlog.step("📂", "Posições ainda abertas", f"{len(open_positions)}: {open_positions}")
        vlog.ok(f"Trades completos ficaram gravados em {CSV_PATH} -- pronto pra análise.")


if __name__ == "__main__":
    main()
