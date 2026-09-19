"""Configuração de logging do bot: console + arquivo diário + tabela `bot_logs`
no Postgres (Neon), pra poder consultar/analisar o desempenho depois.

- Arquivo: `<log_dir>/bot-AAAA-MM-DD.log`, um por dia (sem rotação por
  renomeio -- a pasta do projeto fica no OneDrive, que pode travar o rename).
  Arquivos mais antigos que `log_retention_days` são apagados.
- Banco: `DbLogHandler` empilha as linhas numa fila e uma thread de fundo grava
  em lote. Falha de banco NUNCA propaga: as linhas ficam em buffer (limitado) e
  são reenviadas no próximo lote; se a fila encher, as mais novas são descartadas.
- `cycle_id_var`: o ciclo em andamento, gravado junto de cada linha
  (`asyncio.to_thread` propaga o contexto, então vale também nas threads dos agentes).
"""
from __future__ import annotations

import contextvars
import datetime as dt
import logging
import queue
import sys
import threading
from pathlib import Path

cycle_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("cycle_id", default=None)

_FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MAX_MESSAGE_CHARS = 4000
_MAX_BUFFERED_ROWS = 5000
_BATCH_SIZE = 200
_FLUSH_SECONDS = 5.0
_PURGE_EVERY_SECONDS = 24 * 3600


class DailyFileHandler(logging.FileHandler):
    """Um arquivo por dia (`bot-AAAA-MM-DD.log`); troca de arquivo na virada do dia."""

    def __init__(self, directory: Path, retention_days: int = 30) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._retention_days = retention_days
        self._day = dt.date.today()
        super().__init__(self._path_for(self._day), encoding="utf-8", delay=True)
        self._purge_old_files()

    def _path_for(self, day: dt.date) -> str:
        return str(self._directory / f"bot-{day.isoformat()}.log")

    def emit(self, record: logging.LogRecord) -> None:
        today = dt.date.today()
        if today != self._day:
            self.acquire()
            try:
                self.close()  # fecha o stream do dia anterior (FileHandler reabre sozinho, delay)
                self._day = today
                self.baseFilename = self._path_for(today)
            finally:
                self.release()
            self._purge_old_files()
        super().emit(record)

    def _purge_old_files(self) -> None:
        cutoff = dt.date.today() - dt.timedelta(days=self._retention_days)
        for path in self._directory.glob("bot-*.log"):
            try:
                day = dt.date.fromisoformat(path.stem.removeprefix("bot-"))
            except ValueError:
                continue
            if day < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass


class DbLogHandler(logging.Handler):
    """Grava os logs na tabela `bot_logs` em lote, numa thread de fundo."""

    def __init__(self, retention_days: int = 30) -> None:
        super().__init__()
        self._retention_days = retention_days
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=10000)
        self._buffer: list[dict] = []  # linhas que falharam ao gravar, reenviadas no próximo lote
        self._stop_event = threading.Event()
        self._last_purge = 0.0
        self._last_error_print = 0.0
        self._thread = threading.Thread(target=self._run, name="db-log-writer", daemon=True)
        self._thread.start()

    # -- lado do logging (thread que chama logger.info etc.) -----------------
    def emit(self, record: logging.LogRecord) -> None:
        try:
            from config.settings import settings

            message = self.format(record)
            if len(message) > _MAX_MESSAGE_CHARS:
                message = message[:_MAX_MESSAGE_CHARS] + "…[truncado]"
            self._queue.put_nowait({
                "timestamp": dt.datetime.fromtimestamp(record.created, tz=dt.timezone.utc),
                "level": record.levelname,
                "logger": record.name,
                "cycle_id": cycle_id_var.get(),
                "dry_run": settings.dry_run,
                "message": message,
            })
        except Exception:  # fila cheia ou qualquer outra coisa: descarta, nunca propaga
            pass

    # -- thread de fundo ---------------------------------------------------
    def _run(self) -> None:
        import time

        while not self._stop_event.is_set():
            self._drain(wait=_FLUSH_SECONDS)
            self._flush()
            if time.monotonic() - self._last_purge > _PURGE_EVERY_SECONDS:
                self._purge()
                self._last_purge = time.monotonic()

    def _drain(self, wait: float = 0.0) -> None:
        try:
            self._buffer.append(self._queue.get(timeout=wait) if wait else self._queue.get_nowait())
        except queue.Empty:
            return
        if wait:
            self._stop_event.wait(1.0)  # junta o que chegar no próximo segundo -> lote em vez de 1 INSERT por linha
        while len(self._buffer) < _MAX_BUFFERED_ROWS:
            try:
                self._buffer.append(self._queue.get_nowait())
            except queue.Empty:
                break

    def _flush(self) -> None:
        while self._buffer:
            batch = self._buffer[:_BATCH_SIZE]
            try:
                from sqlalchemy import insert

                from db.models import BotLog
                from db.session import get_session

                with get_session() as session:
                    session.execute(insert(BotLog), batch)
            except Exception as exc:
                self._report_error(exc)
                del self._buffer[:-_MAX_BUFFERED_ROWS]  # limita o que fica retido em memória
                return
            del self._buffer[: len(batch)]

    def _purge(self) -> None:
        try:
            from sqlalchemy import delete

            from db.models import BotLog
            from db.session import get_session

            cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=self._retention_days)
            with get_session() as session:
                session.execute(delete(BotLog).where(BotLog.timestamp < cutoff))
        except Exception as exc:
            self._report_error(exc)

    def _report_error(self, exc: Exception) -> None:
        # stderr direto (não logging -- evitaria recursão) e no máximo 1x/min
        import time

        now = time.monotonic()
        if now - self._last_error_print > 60:
            self._last_error_print = now
            print(f"[db-log-writer] falha ao gravar logs no banco ({type(exc).__name__}: {exc}); "
                  f"{len(self._buffer)} linha(s) em buffer, tenta de novo no próximo lote.", file=sys.stderr)

    def close(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=10)
        self._drain()
        self._flush()
        super().close()


class _DedupFilter(logging.Filter):
    """Descarta uma linha idêntica à anterior emitida há menos de 2s. O código
    tem vários pontos que chamam `logger.warning(x)` e `vlog.warn(x)` com o
    mesmo texto (o vlog imprime no console, o logger vai pro arquivo/banco) --
    sem isto a linha aparecia duplicada no arquivo e na tabela bot_logs."""

    def __init__(self) -> None:
        super().__init__()
        self._last: tuple[str, float] | None = None

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        now = record.created
        if self._last and self._last[0] == message and now - self._last[1] < 2.0:
            return False
        self._last = (message, now)
        return True


class _ExcludeLogger(logging.Filter):
    """Tira do console o que já é impresso direto pelo vlog (evita linha duplicada)."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name != self._name


def configure_logging() -> None:
    from config.settings import settings

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_FILE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(_ExcludeLogger("ivanvestai.vlog"))
    root.addHandler(console)

    log_dir = Path(settings.log_dir)
    if not log_dir.is_absolute():
        log_dir = Path(__file__).resolve().parent.parent / log_dir
    try:
        file_handler = DailyFileHandler(log_dir, settings.log_retention_days)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(_DedupFilter())
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("Não consegui abrir o arquivo de log em %s: %s", log_dir, exc)

    if settings.log_to_db:
        try:
            from db.models import BotLog
            from db.session import engine

            BotLog.__table__.create(bind=engine, checkfirst=True)
            db_handler = DbLogHandler(settings.log_retention_days)
            db_handler.setFormatter(logging.Formatter("%(message)s"))
            db_handler.addFilter(_DedupFilter())
            root.addHandler(db_handler)
        except Exception as exc:
            root.warning("Logs no banco desativados (%s: %s) -- seguindo só com arquivo/console.", type(exc).__name__, exc)

    # Bibliotecas muito falantes em INFO (uma linha por request HTTP / por execução de job).
    for noisy in ("httpx", "httpcore", "urllib3", "apscheduler", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
