"""Cruza a carteira atual com as oportunidades aprovadas tecnicamente e
valida contra as regras de capital: teto de 50%/operação, correlação,
diversificação setorial, saldo de BNB pra taxa, valor mínimo de ordem."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import pandas as pd

from agents.base import BaseAgent
from agents.market_scanner_agent import ScannerOpportunity
from config.settings import settings
from core import vlog
from core.binance_client import BinanceClient, binance_client
from core.indicators import rolling_correlation
from core.risk_rules import correlation_ok, max_allocation_ok, sector_of, suggested_order_value
from db.models import Position


# Achado 24/09/2026 (arquitetura-tecnica.md 9.21 item 9): falha ao calcular
# correlação (rede, par ilíquido, erro de processamento) caía silenciosamente
# pra corr=0.0 -- exatamente o valor que MAIS libera a oportunidade (nenhuma
# correlação = sem bloqueio). Ou seja, um erro transitório na API virava
# aprovação automática, o oposto do que devia. Agora tenta de novo algumas
# vezes; só se persistir é que rejeita por segurança (decisão do Ivan).
MAX_CORRELATION_ATTEMPTS = 2
CORRELATION_RETRY_DELAY_SECONDS = 2

# Achado 25/09/2026: um valor sugerido só um pouco acima do minNotional da
# Binance passava nesta checagem mas falhava na execucao real com
# "Filter failure: NOTIONAL" -- o arredondamento PRA BAIXO da quantidade pro
# step size do par (core.risk_rules.round_step_size, aplicado em
# cycle_runner.py) e a variacao de preco entre este calculo e a execucao de
# fato (depois do ViabilityAgent/RiskCommitteeAgent, que levam varios
# segundos de chamada de LLM) podem reduzir o valor realmente enviado pra
# Binance abaixo do minimo, mesmo quando o valor calculado aqui parecia
# suficiente. Rodou 5x seguidas com a CONTA IVAN perto do saldo minimo
# livre (25/09/2026) -- uma margem de seguranca evita reprovar só depois de
# já ter gasto a chamada de LLM do comite e de ter disparado o alerta
# critico de "a ordem pode ter executado mesmo assim".
MIN_NOTIONAL_SAFETY_MARGIN_PCT = 0.05


@dataclass
class PortfolioCheck:
    approved: bool
    suggested_order_value_usdt: float
    reasons: list[str]


class PortfolioComparisonAgent(BaseAgent):
    name = "portfolio_comparison_agent"
    model = ""  # regras determinísticas de portfólio, sem LLM

    def __init__(
        self,
        *,
        binance: BinanceClient | None = None,
        safety_stablecoin: str | None = None,
        max_allocation_pct_per_trade: float | None = None,
    ) -> None:
        # Multi-conta (multi-conta-plano.md, Fase B/C): parâmetros opcionais,
        # caem pro global de hoje se omitidos -- ver comentário em
        # execution_agent.py. `meme_coin_max_allocation_pct` continua global
        # (ainda não é campo do RuntimeConfig/dashboard por conta).
        super().__init__()
        self._binance = binance if binance is not None else binance_client
        self._safety_stablecoin = safety_stablecoin if safety_stablecoin is not None else settings.safety_stablecoin
        self._max_allocation_pct_per_trade = (
            max_allocation_pct_per_trade if max_allocation_pct_per_trade is not None else settings.max_allocation_pct_per_trade
        )

    def _fetch_klines_with_retry(self, pair: str) -> pd.DataFrame | None:
        """Busca klines diárias do par com retry (mesmo padrão de
        _correlation_with_retry). Achado 24/09/2026 (arquitetura-tecnica.md
        9.20, Fase 5, "klines repetidas desnecessariamente"): antes o par da
        OPORTUNIDADE (fixo dentro de uma chamada de check()) era buscado de
        novo a cada posição aberta comparada -- com N posições abertas, N
        chamadas idênticas à Binance pro mesmo par/timeframe. Agora buscado
        uma única vez por chamada de check() e reaproveitado."""
        last_error: Exception | None = None
        for attempt in range(1, MAX_CORRELATION_ATTEMPTS + 1):
            try:
                return self._binance.get_klines_df(pair, "1d", limit=45)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < MAX_CORRELATION_ATTEMPTS:
                    time.sleep(CORRELATION_RETRY_DELAY_SECONDS)
        vlog.warn(f"Klines de {pair} falharam após {MAX_CORRELATION_ATTEMPTS} tentativas: {last_error}")
        return None

    def _correlation_with_retry(self, df_a: pd.DataFrame, pair_a: str, pair_b: str) -> float | None:
        """Calcula a correlação entre dois pares, tentando de novo em caso de falha
        (rede, par temporariamente sem dados). Retorna None se persistir -- o
        chamador decide o que fazer (não deve tratar None como "sem correlação").
        `df_a` (klines do par da oportunidade) já vem pronto do chamador -- só
        `df_b` (klines da posição aberta sendo comparada, diferente a cada
        iteração) é buscado aqui."""
        last_error: Exception | None = None
        for attempt in range(1, MAX_CORRELATION_ATTEMPTS + 1):
            try:
                df_b = self._binance.get_klines_df(pair_b, "1d", limit=45)
                corr = rolling_correlation(df_a.set_index("open_time")["close"], df_b.set_index("open_time")["close"])
                if math.isnan(corr):
                    # Achado 24/09/2026 (arquitetura-tecnica.md 9.21 item 16): preço
                    # constante nas últimas velas de um dos pares (variância zero)
                    # faz .corr() devolver NaN, não uma exceção. `abs(nan) <= threshold`
                    # é sempre False em Python -- sem essa checagem isso já reprovava
                    # por segurança (correlation_ok(nan) é False), mas tratar como
                    # tentativa falha primeiro segue o mesmo caminho de retry-então-
                    # reprova do resto da função, em vez de um "Correlação alta (nan)"
                    # confuso no motivo registrado.
                    raise ValueError(f"Correlação {pair_a}/{pair_b} resultou em NaN (variância zero no período)")
                return corr
            except Exception as exc:  # noqa: BLE001 -- qualquer falha de rede/dados, tenta de novo
                last_error = exc
                if attempt < MAX_CORRELATION_ATTEMPTS:
                    time.sleep(CORRELATION_RETRY_DELAY_SECONDS)
        vlog.warn(f"Correlação {pair_a}/{pair_b} falhou após {MAX_CORRELATION_ATTEMPTS} tentativas: {last_error}")
        return None

    def check(
        self,
        opportunity: ScannerOpportunity,
        total_equity_usdt: float,
        open_positions: list[Position],
        available_stablecoin: float | None = None,
        meme_coin_eligible: bool = False,
    ) -> PortfolioCheck:
        reasons: list[str] = []
        approved = True

        base_asset = opportunity.pair.removesuffix(self._safety_stablecoin)

        # 1. Tamanho da operação: até `max_allocation_pct_per_trade` do patrimônio
        # TOTAL, limitado ao saldo LIVRE na stablecoin de segurança (a maior parte
        # do patrimônio pode estar em outros ativos, não em USDT disponível --
        # -2010 "insufficient balance" na primeira ordem real, ver
        # arquitetura-tecnica.md 9.13). Antes, valor acima do saldo livre
        # REPROVAVA a oportunidade; agora a ordem é reduzida pra caber, e só é
        # reprovada se ficar abaixo do mínimo da Binance (checagem 2).
        #
        # Achado 24/09/2026 (arquitetura-tecnica.md 9.21 item 13): quando o
        # scanner+ciclo marcam a oportunidade como "meme coin" (2 de 3 sinais
        # elevados -- volume, sentimento social, momentum; ver
        # core.risk_rules.meme_coin_eligible), usa um teto mais apertado
        # (settings.meme_coin_max_allocation_pct, 5% por padrão) em vez do teto
        # normal -- cesta de alto risco tolerado, não o mesmo tamanho de
        # qualquer outra operação.
        max_pct = settings.meme_coin_max_allocation_pct if meme_coin_eligible else self._max_allocation_pct_per_trade
        uncapped_value = total_equity_usdt * max_pct
        suggested_value = suggested_order_value(total_equity_usdt, max_pct, available_stablecoin)
        if not max_allocation_ok(suggested_value, total_equity_usdt, max_pct):
            approved = False
            reasons.append("Valor sugerido excede o teto de alocação por operação.")
        elif suggested_value < uncapped_value:
            reasons.append(
                f"Valor reduzido de ${uncapped_value:.2f} pra ${suggested_value:.2f} pra caber no saldo livre "
                f"em {self._safety_stablecoin} (${available_stablecoin:.2f})."
            )
        if meme_coin_eligible:
            reasons.append(f"Elegível como meme coin/alto risco -- teto de alocação reduzido pra {max_pct:.0%}.")

        # 2. Valor mínimo de ordem da Binance (minNotional)
        try:
            filters = self._binance.get_symbol_filters(opportunity.pair)
            min_notional = float((filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}).get("minNotional", 5.0))
        except Exception:
            min_notional = 5.0
        min_notional_with_margin = min_notional * (1 + MIN_NOTIONAL_SAFETY_MARGIN_PCT)
        if suggested_value < min_notional_with_margin:
            approved = False
            reasons.append(
                f"Valor sugerido (${suggested_value:.2f}) não deixa margem de segurança suficiente acima do "
                f"mínimo da Binance (${min_notional:.2f}) -- precisa de pelo menos ${min_notional_with_margin:.2f} "
                "pra sobreviver ao arredondamento de lote e à variação de preço até a execução real."
            )

        # 3. Correlação com posições já abertas
        open_now = [p for p in open_positions if p.status == "open"]
        df_opportunity = self._fetch_klines_with_retry(opportunity.pair) if open_now else None
        for position in open_now:
            if df_opportunity is None:
                approved = False
                reasons.append(
                    f"Não foi possível calcular correlação com posição aberta em {position.pair} "
                    "após retries -- reprovado por segurança."
                )
                continue
            corr = self._correlation_with_retry(df_opportunity, opportunity.pair, position.pair)
            if corr is None:
                approved = False
                reasons.append(
                    f"Não foi possível calcular correlação com posição aberta em {position.pair} "
                    "após retries -- reprovado por segurança."
                )
                continue
            if not correlation_ok(corr):
                approved = False
                reasons.append(f"Correlação alta ({corr:.2f}) com posição aberta em {position.pair}.")

        # 4. Diversificação setorial (aviso, não bloqueia sozinho — só soma como sinal negativo)
        sector = sector_of(base_asset)
        same_sector_count = sum(1 for p in open_positions if p.status == "open" and sector_of(p.pair.removesuffix(self._safety_stablecoin)) == sector)
        if same_sector_count >= 2:
            reasons.append(f"Já há {same_sector_count} posições no setor '{sector}' — considerar diversificação.")

        if not reasons:
            reasons.append("Dentro de todos os limites de capital, liquidez e correlação.")

        return PortfolioCheck(approved=approved, suggested_order_value_usdt=suggested_value, reasons=reasons)
