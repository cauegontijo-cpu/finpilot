"""Agendador simples (serviço `scheduler` do compose): executa os jobs periodicamente.

Em produção pode ser substituído por cron/systemd timers chamando os mesmos módulos.
"""

from __future__ import annotations

import logging
import signal
import threading
from types import FrameType

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.jobs import maintenance, recurrences

logger = logging.getLogger("finpilot.scheduler")
RECURRENCE_INTERVAL_SECONDS = 15 * 60
MAINTENANCE_EVERY_N_CYCLES = 4 * 6  # a cada ~6 horas

_stop = threading.Event()


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    logger.info("sinal recebido; encerrando agendador", extra={"signal": signum})
    _stop.set()


def main() -> None:
    configure_logging(get_settings().log_level)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    cycle = 0
    while not _stop.is_set():
        try:
            recurrences.run_once()
            if cycle % MAINTENANCE_EVERY_N_CYCLES == 0:
                maintenance.run_once()
        except SQLAlchemyError:
            # Falha temporária (ex.: banco indisponível): a próxima execução recupera o pendente.
            logger.exception("falha temporária no ciclo do agendador")
        cycle += 1
        _stop.wait(RECURRENCE_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
