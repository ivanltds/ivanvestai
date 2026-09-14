import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


class JSONFormatter(logging.Formatter):
    """
    Formatador de logs estruturados em JSON para observabilidade
    completa de operações financeiras e telemetria do IvanvestAI.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Anexar propriedades estruturadas enviadas via extra={'data': ...}
        if hasattr(record, "structured_data") and isinstance(record.structured_data, dict):
            log_data.update(record.structured_data)

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False)


def setup_structured_logger(name: str = "IvanvestAI", log_dir: str = "logs") -> logging.Logger:
    """
    Configura e retorna um logger estruturado que grava em JSONL e no console.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Evitar duplicidade de handlers em reexecuções
    if logger.handlers:
        return logger

    # Cria diretório de logs se não existir
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    json_formatter = JSONFormatter()

    # 1. File Handler (JSONL rotativo por data)
    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    file_handler = logging.FileHandler(
        log_path / f"dca_execution_{current_date}.jsonl",
        encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(json_formatter)
    logger.addHandler(file_handler)

    # 2. Console Handler (legível e estruturado)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(json_formatter)
    logger.addHandler(console_handler)

    return logger


def log_event(logger: logging.Logger, level: str, event_name: str, message: str, **kwargs: Any) -> None:
    """
    Helper para emissão padronizada de eventos de telemetria.
    """
    extra_data = {
        "event": event_name,
        **kwargs
    }
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.log(log_level, message, extra={"structured_data": extra_data})
